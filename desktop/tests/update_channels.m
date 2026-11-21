#import "../jard_updater.h"
#import <Security/Security.h>
#import <assert.h>
#import <sys/stat.h>

@interface JardUpdater (FixtureCallbacks)
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error;
- (void)setState:(NSString *)phase message:(NSString *)message;
@end

@interface ChannelFixtureUpdater : JardUpdater
@property(nonatomic, strong) NSData *certificate;
@property(nonatomic, copy) dispatch_block_t onStaging;
@end

@implementation ChannelFixtureUpdater
- (void)setState:(NSString *)phase message:(NSString *)message {
    [super setState:phase message:message];
    if ([phase isEqual:@"staging"] && self.onStaging) {
        dispatch_block_t callback = self.onStaging;
        self.onStaging = nil;
        callback();
    }
}

- (void)URLSession:(NSURLSession *)session didReceiveChallenge:(NSURLAuthenticationChallenge *)challenge
    completionHandler:(void (^)(NSURLSessionAuthChallengeDisposition, NSURLCredential *))complete {
    SecTrustRef trust = challenge.protectionSpace.serverTrust;
    if (!trust || ![challenge.protectionSpace.host isEqual:@"localhost"]) {
        complete(NSURLSessionAuthChallengeCancelAuthenticationChallenge, nil);
        return;
    }
    SecCertificateRef anchor = SecCertificateCreateWithData(NULL, (__bridge CFDataRef)self.certificate);
    assert(anchor);
    SecTrustSetAnchorCertificates(trust, (__bridge CFArrayRef)@[(__bridge id)anchor]);
    SecTrustSetAnchorCertificatesOnly(trust, true);
    CFRelease(anchor);
    CFErrorRef failure = NULL;
    if (SecTrustEvaluateWithError(trust, &failure))
        complete(NSURLSessionAuthChallengeUseCredential, [NSURLCredential credentialForTrust:trust]);
    else {
        NSLog(@"Fixture TLS trust failed: %@", CFBridgingRelease(failure));
        complete(NSURLSessionAuthChallengeCancelAuthenticationChallenge, nil);
    }
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task didCompleteWithError:(NSError *)error {
    if (error) NSLog(@"Fixture HTTPS request failed: %@", error);
    [super URLSession:session task:task didCompleteWithError:error];
}
@end

static NSDictionary *awaitPhase(JardUpdater *updater, NSString *phase) {
    NSDate *until = [NSDate dateWithTimeIntervalSinceNow:22];
    while ([until timeIntervalSinceNow] > 0) {
        NSDictionary *status = [updater status];
        if ([status[@"phase"] isEqual:phase]) return status;
        [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    }
    NSLog(@"Expected %@. Reached %@", phase, [updater status]);
    abort();
}

static void writeAt(NSURL *root, NSString *name, NSString *value) {
    assert([value writeToURL:[root URLByAppendingPathComponent:name] atomically:YES encoding:NSUTF8StringEncoding error:nil]);
}

static ChannelFixtureUpdater *makeUpdater(NSBundle *bundle, NSURL *root) {
    ChannelFixtureUpdater *updater = [[ChannelFixtureUpdater alloc] initWithConfiguration:
        [bundle objectForInfoDictionaryKey:@"JardUpdates"] bundle:bundle stateChanged:nil];
    assert(updater);
    updater.certificate = [NSData dataWithContentsOfURL:[root URLByAppendingPathComponent:@"server.cer"]];
    assert(updater.certificate.length);
    return updater;
}

static NSUInteger countStages(NSURL *root) {
    NSArray *files = [NSFileManager.defaultManager contentsOfDirectoryAtURL:root includingPropertiesForKeys:nil options:0 error:nil];
    NSUInteger count = 0;
    for (NSURL *file in files) if ([file.lastPathComponent hasPrefix:@".jard-update-"]) count++;
    return count;
}

static void assertNoStage(NSURL *root) {
    assert(countStages(root) == 0);
}

int main(void) {
    @autoreleasepool {
        NSBundle *bundle = NSBundle.mainBundle;
        NSURL *root = bundle.bundleURL.URLByStandardizingPath.URLByDeletingLastPathComponent;
        NSString *version = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"];
        ChannelFixtureUpdater *updater = makeUpdater(bundle, root);
        NSDictionary *state = [updater status];
        assert(![state[@"staged"] boolValue] && ![state[@"updatesDisabled"] boolValue]);
        NSString *channel = state[@"installedChannel"];
        assert(([state[@"channels"] isEqual:@[@"stable", @"dev"]]));
        NSString *failure = nil;
        if ([[bundle objectForInfoDictionaryKey:@"FixtureFlavor"] isEqual:@"cache-installed"]) {
            writeAt(root, @"cache-mode", @"unsolicited");
            [updater check];
            assert([awaitPhase(updater, @"error")[@"message"] containsString:@"matching signed copy"]);

            writeAt(root, @"cache-mode", @"current");
            for (NSUInteger i = 0; i < 2; ++i) {
                [updater check];
                assert(![awaitPhase(updater, @"current")[@"message"] length]);
            }

            writeAt(root, @"cache-mode", @"stable");
            [updater check];
            assert([awaitPhase(updater, @"ready")[@"version"] isEqual:@"4.0.0"]);
            [updater check];
            state = awaitPhase(updater, @"ready");
            assert([state[@"version"] isEqual:@"4.0.0"] && ![state[@"message"] length]);
            __block BOOL checked = NO;
            [updater checkBeforeRestart:^{ checked = YES; }];
            state = awaitPhase(updater, @"ready");
            assert(checked && [state[@"version"] isEqual:@"4.0.0"] && ![state[@"message"] length]);

            writeAt(root, @"cache-mode", @"redirect");
            [updater check];
            assert([awaitPhase(updater, @"ready")[@"version"] isEqual:@"5.0.0"]);
            [updater check];
            state = awaitPhase(updater, @"ready");
            assert([state[@"version"] isEqual:@"5.0.0"] && ![state[@"message"] length]);

            writeAt(root, @"cache-mode", @"wrong-tag");
            [updater check];
            assert([awaitPhase(updater, @"ready")[@"message"] containsString:@"matching signed copy"]);
            writeAt(root, @"cache-mode", @"malformed");
            for (NSUInteger i = 0; i < 2; ++i) {
                [updater check];
                assert(![awaitPhase(updater, @"ready")[@"message"] length]);
            }

            writeAt(root, @"cache-mode", @"stable");
            [updater check];
            assert(![awaitPhase(updater, @"ready")[@"message"] length]);
            writeAt(root, @"cache-mode", @"invalid-signature");
            for (NSUInteger i = 0; i < 2; ++i) {
                [updater check];
                assert([awaitPhase(updater, @"ready")[@"message"] containsString:@"signed update feed"]);
            }
            writeAt(root, @"cache-mode", @"stable");
            [updater check];
            awaitPhase(updater, @"ready");
            writeAt(root, @"cache-mode", @"unsolicited");
            checked = NO;
            [updater checkBeforeRestart:^{ checked = YES; }];
            state = awaitPhase(updater, @"ready");
            assert(checked && [state[@"staged"] boolValue] && [state[@"message"] containsString:@"matching signed copy"]);
            writeAt(root, @"cache-mode", @"stable");
            [updater check];
            awaitPhase(updater, @"ready");
            writeAt(root, @"cache-mode", @"switch-channel");
            assert([updater selectChannel:@"dev" reason:&failure]);
            assert([awaitPhase(updater, @"switch")[@"channel"] isEqual:@"dev"]);
            [updater cancel];
            return 0;
        }
        if ([[bundle objectForInfoDictionaryKey:@"FixtureFlavor"] isEqual:@"resume-installed"]) {
            NSString *behavior = [NSString stringWithContentsOfURL:[root URLByAppendingPathComponent:@"resume-behavior"]
                encoding:NSUTF8StringEncoding error:nil];
            BOOL succeeds = [behavior isEqual:@"resume"] || [behavior isEqual:@"full"];
            [updater check];
            state = awaitPhase(updater, succeeds ? @"ready" : @"error");
            if (succeeds) assert([state[@"staged"] boolValue] && [state[@"version"] isEqual:[behavior isEqual:@"full"] ? @"7.0.0" : @"6.0.0"]);
            else {
                NSString *reason = [behavior isEqual:@"tamper"] ? @"signed size and digest" : @"resume";
                assert(![state[@"staged"] boolValue] && [state[@"message"] containsString:reason]);
            }
            [updater cancel];
            return 0;
        }
        if ([version isEqual:@"3.0.0"]) {
            assert([channel isEqual:@"stable"] && [state[@"channel"] isEqual:@"stable"]);
            assert(![updater selectChannel:@"other" reason:&failure]);
            [NSUserDefaults.standardUserDefaults setBool:YES forKey:@"DisableUpdates"];
            [updater check];
            assert([awaitPhase(updater, @"unavailable")[@"updatesDisabled"] boolValue]);
            assert(![updater selectChannel:@"dev" reason:&failure]);
            [NSUserDefaults.standardUserDefaults removeObjectForKey:@"DisableUpdates"];
            [updater check];
            awaitPhase(updater, @"current");  // The older release may only be used on a different channel.
            writeAt(root, @"stable-feed", @"stable-four");
            [updater check];
            NSDictionary *stableStage = awaitPhase(updater, @"ready");
            assert([stableStage[@"version"] isEqual:@"4.0.0"]);
            assert([stableStage[@"stageChannel"] isEqual:@"stable"] && [stableStage[@"notes"] isEqual:@"Notes for stable-four"]);
            assert(![stableStage[@"releaseNotesAvailable"] boolValue]);
            [updater cancel];
            updater = makeUpdater(bundle, root);
            assert([updater restoreStagedUpdate]);
            assert([[updater status][@"notes"] isEqual:@"Notes for stable-four"]);
            // A final check must supersede even a stable release that was just verified.
            writeAt(root, @"stable-feed", @"stable-five");
            __block NSUInteger finalChecks = 0;
            [updater check];
            assert([updater canInstallStagedUpdate]);
            [updater checkBeforeRestart:^{ finalChecks++; }];
            assert([[[updater status] objectForKey:@"phase"] isEqual:@"checking"] && finalChecks == 0);
            stableStage = awaitPhase(updater, @"ready");
            assert([stableStage[@"version"] isEqual:@"5.0.0"] && finalChecks == 1);
            assert([stableStage[@"notes"] isEqual:@"Notes for stable-five"]);

            writeAt(root, @"stable-feed", @"stalled-feed");
            NSTimeInterval start = NSProcessInfo.processInfo.systemUptime;
            [updater checkBeforeRestart:^{ finalChecks++; }];
            NSDictionary *offline = awaitPhase(updater, @"ready");
            NSTimeInterval elapsed = NSProcessInfo.processInfo.systemUptime - start;
            assert(elapsed >= 13 && elapsed < 20 && finalChecks == 2);
            assert([offline[@"version"] isEqual:@"5.0.0"] && [offline[@"staged"] boolValue] && [offline[@"message"] length]);
            writeAt(root, @"stable-feed", @"stable");
            printf("A fresh Stable release replaced a recent stage. A trickling feed timed out in %.1f seconds and kept that stage.\n", elapsed);

            writeAt(root, @"development-feed", @"invalid-key");
            assert([updater selectChannel:@"dev" reason:&failure]);
            assert([awaitPhase(updater, @"error")[@"message"] containsString:@"signed update feed"]);
            assertNoStage(root);

            writeAt(root, @"development-feed", @"first");
            assert(chmod(root.fileSystemRepresentation, 0500) == 0);
            [updater check];
            assert([awaitPhase(updater, @"download")[@"version"] isEqual:@"2.0.0"]);
            assert(![updater installOnQuit:&failure]);
            assert(chmod(root.fileSystemRepresentation, 0700) == 0);
            [updater check];
            NSDictionary *first = awaitPhase(updater, @"switch");
            assert([first[@"version"] isEqual:@"2.0.0"]);
            assert(![updater confirmChannelSwitch:@"stale" reason:&failure]);
            assert(![updater installOnQuit:&failure]);
            assertNoStage(root);

            writeAt(root, @"development-feed", @"second");
            assert([updater confirmChannelSwitch:first[@"offer"] reason:&failure]);
            NSDictionary *second = awaitPhase(updater, @"switch");
            assert([second[@"version"] isEqual:first[@"version"]]);
            assert(![second[@"offer"] isEqual:first[@"offer"]]);
            assertNoStage(root);

            writeAt(root, @"development-feed", @"wrong-bundle");
            [updater check];
            NSDictionary *foreign = awaitPhase(updater, @"switch");
            assert([updater confirmChannelSwitch:foreign[@"offer"] reason:&failure]);
            assert([awaitPhase(updater, @"error")[@"message"] containsString:@"different update channel"]);
            assertNoStage(root);

            writeAt(root, @"development-feed", @"second");
            [updater check];
            assert([updater confirmChannelSwitch:awaitPhase(updater, @"switch")[@"offer"] reason:&failure]);
            NSDictionary *devStage = awaitPhase(updater, @"ready");
            assert([devStage[@"staged"] boolValue] && [devStage[@"stageChannel"] isEqual:@"dev"]);
            assert([devStage[@"releaseNotesAvailable"] boolValue] && [devStage[@"notes"] isEqual:@"Notes for dev-second"]);
            assert(![updater openStagedReleaseNotesForVersion:@"5.0.0" channel:@"dev" reason:&failure]);
            assert(![updater openStagedReleaseNotesForVersion:devStage[@"version"] channel:@"stable" reason:&failure]);
            writeAt(root, @"development-feed", @"first");
            __block BOOL checked = NO;
            [updater checkBeforeRestart:^{ checked = YES; }];
            state = [updater status];
            assert([state[@"phase"] isEqual:@"checking"] && [state[@"staged"] boolValue]);
            assert([state[@"notes"] isEqual:@"Notes for dev-second"] && [state[@"releaseNotesAvailable"] boolValue]);
            NSDictionary *updated = awaitPhase(updater, @"switch");
            assert(!updated[@"stageChannel"] && !updated[@"releaseNotesAvailable"]);
            assert(checked && [updated[@"offer"] isEqual:first[@"offer"]] && ![updated[@"staged"] boolValue]);
            assert(![updater restart:&failure]);
            assertNoStage(root);
            assert([updater confirmChannelSwitch:updated[@"offer"] reason:&failure]);
            awaitPhase(updater, @"ready");

            [updater cancel];
            updater = makeUpdater(bundle, root);  // Model a new updater after an interrupted process.
            assert(![updater restoreStagedUpdate]);
            assertNoStage(root);
            [updater check];
            assert([updater confirmChannelSwitch:awaitPhase(updater, @"switch")[@"offer"] reason:&failure]);
            awaitPhase(updater, @"ready");
            [NSUserDefaults.standardUserDefaults setBool:YES forKey:@"DisableUpdates"];
            assert(![updater installOnQuit:&failure]);
            [NSUserDefaults.standardUserDefaults removeObjectForKey:@"DisableUpdates"];
            writeAt(root, @"development-feed", @"missing");
            checked = NO;
            [updater checkBeforeRestart:^{ checked = YES; }];
            NSDictionary *confirmedOffline = awaitPhase(updater, @"ready");
            assert(checked && [confirmedOffline[@"staged"] boolValue] && [confirmedOffline[@"message"] length]);
            writeAt(root, @"development-feed", @"first");
            assert([updater restart:&failure]);
            return 0;
        }
        assert([version isEqual:@"2.0.0"]);
        assertNoStage(root);  // Initializing the new build removes the prior same-path backup.
        assert([state[@"channel"] isEqual:channel]);
        if ([channel isEqual:@"dev"]) {
            [updater check];
            awaitPhase(updater, @"current");
            assert([updater selectChannel:@"stable" reason:&failure]);
            NSDictionary *offer = awaitPhase(updater, @"switch");
            assert([offer[@"version"] isEqual:version]);  // Same version, different channel.
            assert([updater confirmChannelSwitch:offer[@"offer"] reason:&failure]);
            awaitPhase(updater, @"ready");
            assert([updater installOnQuit:&failure]);
            writeAt(root, @"installed-on-quit", @"Development restarted at the same path. Stable installed on quit.");
        } else {
            assert([channel isEqual:@"stable"]);
            writeAt(root, @"stable-reopened", @"Stable opened at the same path and removed the previous backup.");
            writeAt(root, @"stable-feed", @"stable-four");
            [updater check];
            assert([awaitPhase(updater, @"ready")[@"version"] isEqual:@"4.0.0"]);
            writeAt(root, @"stable-feed", @"stable-five");
            __weak ChannelFixtureUpdater *weakUpdater = updater;
            __block BOOL quitting = NO;
            updater.onStaging = ^{
                assert([weakUpdater prepareToInstallOnQuit]);
                quitting = YES;
            };
            [updater check];
            NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:10];
            while ((!quitting || countStages(root) != 1) && [deadline timeIntervalSinceNow] > 0) {
                [NSRunLoop.currentRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
            }
            assert(quitting && countStages(root) == 1);
            assert([updater.status[@"version"] isEqual:@"4.0.0"]);
            assert([updater installOnQuit:&failure]);
            NSDictionary *installed = [NSDictionary dictionaryWithContentsOfURL:
                [bundle.bundleURL URLByAppendingPathComponent:@"Contents/Info.plist"]];
            assert([installed[@"CFBundleVersion"] isEqual:@"4.0.0"]);
            writeAt(root, @"late-stage-fenced", @"Quit kept the verified stage and discarded a later verification result.");
        }
        return 0;
    }
}
