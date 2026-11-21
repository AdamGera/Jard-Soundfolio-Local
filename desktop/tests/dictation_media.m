#import "../jard_dictation_media.h"
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <assert.h>

static JardPlayback *playback(JardPlaybackState state, NSString *item, double position) {
    return [[JardPlayback alloc] initWithState:state item:item position:position];
}

@interface FixturePlayer : NSObject <JardMediaPlayer>
@property(nonatomic, copy) NSString *name;
@property(atomic, strong) JardPlayback *current;
@property(atomic) BOOL granted, declines, available;
@property(atomic) NSUInteger accessRequests, pauses, resumes;
@property(nonatomic, strong) dispatch_semaphore_t reading, continueReading;
+ (instancetype)withName:(NSString *)name;
@end

@implementation FixturePlayer
+ (instancetype)withName:(NSString *)name {
    FixturePlayer *player = self.new;
    player.name = name;
    player.available = player.granted = YES;
    player.current = playback(JardPlaybackPlaying, @"first item", 30);
    return player;
}
- (BOOL)requestAccess { self.accessRequests++; return self.available && self.granted; }
- (JardPlayback *)playback {
    if (self.reading) {
        dispatch_semaphore_signal(self.reading);
        assert(dispatch_semaphore_wait(self.continueReading, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
    }
    return self.available && self.granted ? self.current : nil;
}
- (BOOL)pause {
    if (self.declines) return NO;
    JardPlayback *current = self.current;
    self.current = playback(JardPlaybackPaused, current.item, current.position);
    self.pauses++;
    return YES;
}
- (BOOL)resume {
    JardPlayback *current = self.current;
    self.current = playback(JardPlaybackPlaying, current.item, current.position);
    self.resumes++;
    return YES;
}
@end

static void waitFor(BOOL (^condition)(void)) {
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:2];
    while (!condition() && deadline.timeIntervalSinceNow > 0)
        [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.01]];
    assert(condition());
}

@interface JardAppleEventPlayer : NSObject <JardMediaPlayer>
@property(nonatomic, strong) NSRunningApplication *application;
@property(nonatomic, strong) NSAppleEventDescriptor *target;
@property(nonatomic) AEEventClass playbackClass;
@property(nonatomic) AEEventID pauseEvent, resumeEvent;
@property(nonatomic) OSType trackIdentity;
@end

@interface FixtureEventReceiver : NSObject
@property(nonatomic) BOOL paused;
@property(nonatomic) NSUInteger requests;
@end

@implementation FixtureEventReceiver
- (void)receive:(NSAppleEventDescriptor *)message reply:(NSAppleEventDescriptor *)reply {
    self.requests++;
    if (message.eventClass == 'hook') {
        assert(message.eventID == 'Paus' || message.eventID == 'Play');
        self.paused = message.eventID == 'Paus';
        return;
    }
    NSAppleEventDescriptor *property = [message paramDescriptorForKeyword:keyDirectObject];
    assert(property.descriptorType == typeObjectSpecifier);
    OSType key = [property descriptorForKeyword:keyAEKeyData].typeCodeValue;
    NSAppleEventDescriptor *value;
    if (key == 'pPlS') value = [NSAppleEventDescriptor descriptorWithEnumCode:self.paused ? 'kPSp' : 'kPSP'];
    else if (key == 'pPos') value = [NSAppleEventDescriptor descriptorWithDouble:43.5];
    else {
        assert(key == 'pPIS');
        NSAppleEventDescriptor *track = [property descriptorForKeyword:keyAEContainer];
        assert(track.descriptorType == typeObjectSpecifier && [track descriptorForKeyword:keyAEKeyData].typeCodeValue == 'pTrk');
        value = [NSAppleEventDescriptor descriptorWithString:@"fixture track ID"];
    }
    [reply setParamDescriptor:value forKeyword:keyDirectObject];
}
@end

static void testAppleEvents(void) {
    [NSApplication sharedApplication];
    FixtureEventReceiver *receiver = FixtureEventReceiver.new;
    NSAppleEventManager *manager = NSAppleEventManager.sharedAppleEventManager;
    [manager setEventHandler:receiver andSelector:@selector(receive:reply:) forEventClass:kAECoreSuite andEventID:kAEGetData];
    [manager setEventHandler:receiver andSelector:@selector(receive:reply:) forEventClass:'hook' andEventID:'Paus'];
    [manager setEventHandler:receiver andSelector:@selector(receive:reply:) forEventClass:'hook' andEventID:'Play'];
    // Actual Apple Event transport, addressed only to this fixture process.
    JardAppleEventPlayer *player = JardAppleEventPlayer.new;
    player.application = NSRunningApplication.currentApplication;
    player.target = [NSAppleEventDescriptor descriptorWithProcessIdentifier:NSProcessInfo.processInfo.processIdentifier];
    player.playbackClass = 'hook';
    player.pauseEvent = 'Paus';
    player.resumeEvent = 'Play';
    player.trackIdentity = 'pPIS';
    __block BOOL finished = NO;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        JardPlayback *before = [player playback];
        assert(before && before.state == JardPlaybackPlaying && [before.item isEqual:@"fixture track ID"] && before.position == 43.5);
        assert([player pause]);
        JardPlayback *after = [player playback];
        assert(after && after.state == JardPlaybackPaused);
        assert([player resume]);
        dispatch_async(dispatch_get_main_queue(), ^{ finished = YES; });
    });
    waitFor(^{ return finished; });
    assert(receiver.requests == 8 && !receiver.paused);
    [manager removeEventHandlerForEventClass:kAECoreSuite andEventID:kAEGetData];
    [manager removeEventHandlerForEventClass:'hook' andEventID:'Paus'];
    [manager removeEventHandlerForEventClass:'hook' andEventID:'Play'];
}

int main(void) {
    @autoreleasepool {
        testAppleEvents();
        FixturePlayer *ordinary = [FixturePlayer withName:@"Music"];
        FixturePlayer *alreadyPaused = [FixturePlayer withName:@"Spotify"];
        FixturePlayer *changed = [FixturePlayer withName:@"VLC"];
        FixturePlayer *sought = [FixturePlayer withName:@"Seek"];
        FixturePlayer *manuallyResumed = [FixturePlayer withName:@"Manual"];
        FixturePlayer *closed = [FixturePlayer withName:@"Closed"];
        FixturePlayer *denied = [FixturePlayer withName:@"Denied"];
        FixturePlayer *failed = [FixturePlayer withName:@"Failed"];
        alreadyPaused.current = playback(JardPlaybackPaused, @"first item", 30);
        denied.granted = NO;
        failed.declines = YES;
        NSArray *players = @[ordinary, alreadyPaused, changed, sought, manuallyResumed, closed, denied, failed];
        JardDictationMedia *media = [[JardDictationMedia alloc] initWithPlayers:^{ return players; }];
        [media begin];
        waitFor(^BOOL{ return closed.pauses == 1; });
        changed.current = playback(JardPlaybackPaused, @"second item", 30);
        sought.current = playback(JardPlaybackPaused, @"first item", 70);
        manuallyResumed.current = playback(JardPlaybackPlaying, @"first item", 30);
        closed.available = NO;
        [media finish];
        [media shutdown];
        assert(ordinary.resumes == 1 && !alreadyPaused.pauses && !alreadyPaused.resumes);
        assert(!changed.resumes && !sought.resumes && !manuallyResumed.resumes && !closed.resumes);
        assert(!denied.pauses && !denied.resumes && !failed.pauses && !failed.resumes);
        for (FixturePlayer *player in players) assert(player.accessRequests == 0);

        __block BOOL requested = NO;
        [media requestAccess:^(NSArray<NSString *> *allowed, NSArray<NSString *> *unavailable) {
            NSArray *deniedPlayers = @[@"Closed", @"Denied"];
            assert(NSThread.isMainThread && [allowed containsObject:@"Music"] && [unavailable isEqual:deniedPlayers]);
            requested = YES;
        }];
        waitFor(^{ return requested; });
        for (FixturePlayer *player in players) assert(player.accessRequests == 1);

        FixturePlayer *slow = [FixturePlayer withName:@"Slow"];
        slow.reading = dispatch_semaphore_create(0);
        slow.continueReading = dispatch_semaphore_create(0);
        JardDictationMedia *shortTake = [[JardDictationMedia alloc] initWithPlayers:^{ return @[slow]; }];
        [shortTake begin];
        assert(dispatch_semaphore_wait(slow.reading, dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC)) == 0);
        [shortTake finish];
        dispatch_semaphore_signal(slow.continueReading);
        [shortTake shutdown];
        assert(!slow.pauses && !slow.resumes);
        puts("Media playback verified explicit permission requests and resumed only unchanged playback that Jard paused.");
    }
}
