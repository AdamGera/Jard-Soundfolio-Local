#import "../jard_updater.h"
#import <assert.h>

int main(int argc, const char **argv) {
    @autoreleasepool {
        NSBundle *bundle = NSBundle.mainBundle;
        NSURL *installed = bundle.bundleURL.URLByStandardizingPath;
        NSURL *root = installed.URLByDeletingLastPathComponent;
        NSString *version = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"];
        NSDictionary *configuration = @{@"feed_url": @"https://example.invalid/feed", @"public_key": @"unused"};
        if ([version isEqual:@"1.2.0"]) {
            JardUpdater *updater = [[JardUpdater alloc] initWithConfiguration:configuration bundle:bundle stateChanged:nil];
            assert(updater);
            BOOL backup = [NSFileManager.defaultManager fileExistsAtPath:
                [root URLByAppendingPathComponent:@".jard-update-fixture"].path];
            NSString *proof = backup ? @"new version launched; backup remains" : @"new version launched; backup cleaned";
            [proof writeToURL:[root URLByAppendingPathComponent:@"launched"] atomically:YES encoding:NSUTF8StringEncoding error:nil];
            return 0;
        }
        NSURL *stage = [root URLByAppendingPathComponent:@".jard-update-fixture" isDirectory:YES];
        NSDictionary *pending = @{@"installed": installed.path, @"runningVersion": version,
            @"stage": stage.path, @"candidate": @"Fixture.app", @"version": @"1.2.0", @"notes": @"Isolated test"};
        [NSUserDefaults.standardUserDefaults setObject:pending forKey:@"JardPendingUpdate"];
        JardUpdater *updater = [[JardUpdater alloc] initWithConfiguration:configuration bundle:bundle stateChanged:nil];
        assert([updater restoreStagedUpdate]);
        assert([[updater status][@"phase"] isEqual:@"ready"]);
        NSString *failure = nil;
        BOOL updateOnQuit = argc > 1 && [@(argv[1]) isEqual:@"quit"];
        BOOL applied = updateOnQuit ? [updater installOnQuit:&failure] : [updater restart:&failure];
        if (!applied) {
            fprintf(stderr, "%s\n", failure.UTF8String);
            return 1;
        }
        return 0;
    }
}
