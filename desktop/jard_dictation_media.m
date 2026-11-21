#import "jard_dictation_media.h"
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <math.h>

@implementation JardPlayback
- (instancetype)initWithState:(JardPlaybackState)state item:(NSString *)item position:(double)position {
    if (!(self = [super init])) return nil;
    _state = state;
    _item = [item copy];
    _position = position;
    return self;
}
@end

static NSAppleEventDescriptor *propertySpecifier(OSType code, NSAppleEventDescriptor *container) {
    NSAppleEventDescriptor *key = [NSAppleEventDescriptor descriptorWithTypeCode:code];
    AEDesc object = {typeNull, NULL};
    AEDesc root = *container.aeDesc, selector = *key.aeDesc;
    if (CreateObjSpecifier(typeProperty, &root, formPropertyID, &selector, false, &object) != noErr) return nil;
    return [[NSAppleEventDescriptor alloc] initWithAEDescNoCopy:&object];
}

static BOOL isBoolean(NSAppleEventDescriptor *value) {
    return value && (value.descriptorType == typeBoolean || value.descriptorType == typeTrue || value.descriptorType == typeFalse);
}

@interface JardAppleEventPlayer : NSObject <JardMediaPlayer>
@property(nonatomic, strong) NSRunningApplication *application;
@property(nonatomic, strong) NSAppleEventDescriptor *target;
@property(nonatomic, copy) NSString *name;
@property(nonatomic) AEEventClass playbackClass;
@property(nonatomic) AEEventID pauseEvent, resumeEvent;
@property(nonatomic) OSType trackIdentity;
@property(nonatomic) BOOL vlc;
- (instancetype)initWithApplication:(NSRunningApplication *)app;
@end

@implementation JardAppleEventPlayer

- (instancetype)initWithApplication:(NSRunningApplication *)app {
    if (!(self = [super init]) || app.terminated || app.processIdentifier <= 0) return nil;
    NSString *bundle = app.bundleIdentifier;
    if ([bundle isEqual:@"com.apple.Music"]) {
        _name = @"Music";
        _playbackClass = 'hook';
        _trackIdentity = 'pPIS';
    } else if ([bundle isEqual:@"com.spotify.client"]) {
        _name = @"Spotify";
        _playbackClass = 'spfy';
        _trackIdentity = 'ID  ';
    } else if ([bundle isEqual:@"org.videolan.vlc"]) {
        _name = @"VLC";
        _playbackClass = 'VLC#';
        _vlc = YES;
    } else return nil;
    _pauseEvent = _vlc ? 'VLC1' : 'Paus';
    _resumeEvent = _vlc ? 'VLC1' : 'Play';
    _application = app;
    _target = [NSAppleEventDescriptor descriptorWithProcessIdentifier:app.processIdentifier];
    return _target ? self : nil;
}

- (BOOL)requestAccess {
    return !self.application.terminated &&
        AEDeterminePermissionToAutomateTarget(self.target.aeDesc, typeWildCard, typeWildCard, true) == noErr;
}

- (NSAppleEventDescriptor *)sendClass:(AEEventClass)group event:(AEEventID)code object:(NSAppleEventDescriptor *)object {
    if (self.application.terminated) return nil;
    NSAppleEventDescriptor *event = [NSAppleEventDescriptor appleEventWithEventClass:group eventID:code
        targetDescriptor:self.target returnID:kAutoGenerateReturnID transactionID:kAnyTransactionID];
    if (object) [event setParamDescriptor:object forKeyword:keyDirectObject];
    NSAppleEventDescriptor *reply = [event sendEventWithOptions:NSAppleEventSendWaitForReply |
        NSAppleEventSendNeverInteract | kAEDoNotPromptForUserConsent timeout:.15 error:nil];
    return [reply paramDescriptorForKeyword:keyErrorNumber].int32Value == 0 ? reply : nil;
}

- (NSAppleEventDescriptor *)getProperty:(OSType)code container:(NSAppleEventDescriptor *)container {
    NSAppleEventDescriptor *object = propertySpecifier(code, container ?: NSAppleEventDescriptor.nullDescriptor);
    if (!object) return nil;
    return [[self sendClass:kAECoreSuite event:kAEGetData object:object] paramDescriptorForKeyword:keyDirectObject];
}

- (JardPlayback *)playback {
    NSAppleEventDescriptor *state = [self getProperty:self.vlc ? 'AAPL' : 'pPlS' container:nil];
    JardPlaybackState playing;
    if (!state) return nil;
    if (self.vlc) {
        if (!isBoolean(state)) return nil;
        playing = state.booleanValue ? JardPlaybackPlaying : JardPlaybackPaused;
    } else {
        OSType code = state.enumCodeValue;
        if (code != 'kPSP' && code != 'kPSp') return nil;
        playing = code == 'kPSP' ? JardPlaybackPlaying : JardPlaybackPaused;
    }
    NSAppleEventDescriptor *track = self.vlc ? nil : propertySpecifier('pTrk', NSAppleEventDescriptor.nullDescriptor);
    if (!self.vlc && !track) return nil;
    NSString *item = [self getProperty:self.vlc ? 'AAPA' : self.trackIdentity container:track].stringValue;
    if (!item.length) return nil;
    NSAppleEventDescriptor *time = [[self getProperty:self.vlc ? 'AACT' : 'pPos' container:nil]
        coerceToDescriptorType:typeIEEE64BitFloatingPoint];
    if (!time || !isfinite(time.doubleValue) || time.doubleValue < 0) return nil;
    return [[JardPlayback alloc] initWithState:playing item:item position:time.doubleValue];
}

- (BOOL)sendPlaybackEvent:(AEEventID)event expectedVLCPlaying:(BOOL)playing {
    if (self.vlc) {
        // VLC uses the same toggle for both actions. Recheck before sending it.
        NSAppleEventDescriptor *current = [self getProperty:'AAPL' container:nil];
        if (!isBoolean(current) || current.booleanValue != playing) return NO;
    }
    return [self sendClass:self.playbackClass event:event object:nil] != nil;
}

- (BOOL)pause {
    return [self sendPlaybackEvent:self.pauseEvent expectedVLCPlaying:YES];
}

- (BOOL)resume {
    return [self sendPlaybackEvent:self.resumeEvent expectedVLCPlaying:NO];
}
@end

@interface JardPausedPlayback : NSObject
@property(nonatomic, strong) id<JardMediaPlayer> player;
@property(nonatomic, strong) JardPlayback *playback;
@end

@implementation JardPausedPlayback
@end

@interface JardDictationMedia ()
@property(nonatomic, strong) dispatch_queue_t queue;
@property(nonatomic, copy) NSArray<id<JardMediaPlayer>> * (^runningPlayers)(void);
@property(nonatomic, strong) NSMutableArray<JardPausedPlayback *> *paused;
@property(atomic) BOOL wantsPause;
@end

@implementation JardDictationMedia

- (instancetype)init {
    return [self initWithPlayers:^{
        NSMutableArray *players = NSMutableArray.array;
        NSMutableSet *names = NSMutableSet.set;
        for (NSRunningApplication *app in NSWorkspace.sharedWorkspace.runningApplications) {
            JardAppleEventPlayer *player = [[JardAppleEventPlayer alloc] initWithApplication:app];
            if (player && ![names containsObject:player.name]) {
                [players addObject:player];
                [names addObject:player.name];
            }
        }
        return players;
    }];
}

- (instancetype)initWithPlayers:(NSArray<id<JardMediaPlayer>> * (^)(void))runningPlayers {
    if (!(self = [super init])) return nil;
    _queue = dispatch_queue_create("ai.jard.dictation-media", DISPATCH_QUEUE_SERIAL);
    _runningPlayers = [runningPlayers copy];
    _paused = NSMutableArray.array;
    return self;
}

- (void)requestAccess:(void (^)(NSArray<NSString *> *, NSArray<NSString *> *))completion {
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSMutableOrderedSet<NSString *> *allowed = NSMutableOrderedSet.orderedSet;
        NSMutableOrderedSet<NSString *> *unavailable = NSMutableOrderedSet.orderedSet;
        for (id<JardMediaPlayer> player in self.runningPlayers()) {
            if ([allowed containsObject:player.name] || [unavailable containsObject:player.name]) continue;
            if ([player requestAccess]) [allowed addObject:player.name];
            else [unavailable addObject:player.name];
        }
        dispatch_async(dispatch_get_main_queue(), ^{ completion(allowed.array, unavailable.array); });
    });
}

- (void)begin {
    self.wantsPause = YES;
    dispatch_async(self.queue, ^{
        if (!self.wantsPause) return;
        for (id<JardMediaPlayer> player in self.runningPlayers()) {
            if (!self.wantsPause) break;
            JardPlayback *before = [player playback];
            if (!before || before.state != JardPlaybackPlaying || !self.wantsPause || ![player pause]) continue;
            JardPlayback *after = [player playback];
            if (after && after.state == JardPlaybackPlaying && [after.item isEqual:before.item]) {
                // Some players acknowledge a command before publishing the new state.
                [NSThread sleepForTimeInterval:.06];
                after = [player playback];
            }
            if (!after || after.state != JardPlaybackPaused || ![after.item isEqual:before.item]) continue;
            JardPausedPlayback *receipt = JardPausedPlayback.new;
            receipt.player = player;
            receipt.playback = after;
            [self.paused addObject:receipt];
        }
    });
}

- (void)resumePausedPlayers {
    for (JardPausedPlayback *receipt in self.paused.copy) {
        if (self.wantsPause) break;
        JardPlayback *current = [receipt.player playback];
        if (self.wantsPause) break;
        if (current && current.state == JardPlaybackPaused && [current.item isEqual:receipt.playback.item] &&
                fabs(current.position - receipt.playback.position) <= .25) [receipt.player resume];
        [self.paused removeObject:receipt];
    }
}

- (void)finish {
    self.wantsPause = NO;
    dispatch_async(self.queue, ^{ [self resumePausedPlayers]; });
}

- (void)shutdown {
    self.wantsPause = NO;
    dispatch_sync(self.queue, ^{ [self resumePausedPlayers]; });
}
@end
