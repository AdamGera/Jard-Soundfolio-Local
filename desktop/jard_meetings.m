#import "jard_meetings.h"
#import "meeting_presence.h"
#import "jard_calendar.h"
#import "jard_meeting_panel.h"
#import "jard_meeting_delta.h"
#import <math.h>

static NSString *const managedMessage = @"Meetings are managed by your organization.";
static NSString *const detectionKey = @"JardDetectMeetings";
static NSString *const policyKey = @"JardMeetingProviderPolicies";
static NSString *const lastSessionKey = @"JardLastMeetingSession";
static NSString *const autoStopKey = @"JardMeetingAutoStop";
static NSString *const indicatorKey = @"JardShowMeetingIndicator";
static NSString *const cornerKey = @"JardMeetingIndicatorCorner";
static NSString *const displayKey = @"JardMeetingIndicatorDisplay";

@interface JardMeetings ()
@property(nonatomic, copy) JardMeetingStart beginCapture;
@property(nonatomic, copy) JardDictationSend send;
@property(nonatomic, copy) void (^changed)(NSDictionary *);
@property(nonatomic, strong) JardMeetingPresence *presence;
@property(nonatomic, strong) JardCalendar *calendar;
@property(nonatomic, strong) JardCalendarMeeting *calendarOffer;
@property(nonatomic, strong) JardCalendarMeeting *activeCalendar, *permissionCalendar;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSDate *> *reminded, *calendarSnooze;
@property(nonatomic, strong) NSDate *reminderExpires;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, strong) JardCall *offered, *activeCall;
@property(nonatomic, strong) JardCall *permissionCall;
@property(nonatomic, copy) NSArray<NSString *> *activeLanguages;
@property(nonatomic, strong) JardMeetingPanel *panel;
@property(nonatomic, strong) NSTextField *caption;
@property(nonatomic, weak) NSTextField *completionBody;
@property(nonatomic, weak) NSButton *incrementalTextButton, *fullTextButton;
@property(nonatomic, weak) NSButton *agentPromptButton;
@property(nonatomic, copy) NSString *phase, *message, *recordingID, *sessionID, *lastSavedSessionID, *providerLabel;
@property(nonatomic, copy) NSString *provisionalTitle, *meetingTitle;
@property(nonatomic) BOOL microphoneSilent;
@property(nonatomic) BOOL systemAudioUnavailable;
@property(nonatomic, copy) NSString *microphoneName;
@property(nonatomic, copy) NSString *completionSession;
@property(nonatomic, copy) NSDictionary *completion;
@property(nonatomic, copy) NSString *recoveryDraft, *permissionSource, *panelCorner;
@property(nonatomic, strong) NSNumber *panelDisplay;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSDictionary *> *policies;
@property(nonatomic, strong) NSMutableArray<NSDictionary *> *liveTurns;
@property(nonatomic, strong) JardMeetingDelta *delta;
@property(nonatomic, copy) NSString *textMeetingID, *exhaustedMeetingID, *emptyMeetingID;
@property(nonatomic, copy) NSString *excerptFeedback;
@property(nonatomic, copy) NSString *differentCall, *keptSwitch, *endedReason;
@property(nonatomic) BOOL copying;
@property(nonatomic) BOOL detection, workspace, suspended, sampling, closed, endPromptAcknowledged;
@property(nonatomic) BOOL autoStop, automaticRecording;
@property(nonatomic) BOOL showIndicator, recovering, scanningRecovery;
@property(nonatomic) BOOL checkingLatest, waitingForRecoveredMeeting;
@property(nonatomic) BOOL checkingCompletion;
@property(nonatomic) NSTimeInterval offeredAt, lastPresent, seconds, automaticUntil, autoStopAt, differentSince, nextLatestCheck;
@property(nonatomic) NSTimeInterval completionDeadline;
@property(nonatomic) NSUInteger liveCharacters, excerptGeneration, generation, excerptFeedbackGeneration;
@property(nonatomic) NSUInteger completionGeneration;
@end

@implementation JardMeetings

- (instancetype)initWithManaged:(BOOL)managed start:(JardMeetingStart)start send:(JardDictationSend)send
                      changed:(void (^)(NSDictionary *))changed {
    if (!(self = [super init])) return nil;
    _managed = managed;
    _beginCapture = [start copy];
    _send = [send copy];
    _changed = [changed copy];
    _presence = JardMeetingPresence.new;
    _liveTurns = NSMutableArray.array;
    _delta = JardMeetingDelta.new;
    _phase = @"idle";
    _detection = !managed && [NSUserDefaults.standardUserDefaults boolForKey:detectionKey];
    _autoStop = [NSUserDefaults.standardUserDefaults boolForKey:autoStopKey];
    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    _showIndicator = [defaults objectForKey:indicatorKey] ? [defaults boolForKey:indicatorKey] : YES;
    NSString *corner = [defaults stringForKey:cornerKey];
    _panelCorner = JardValidMeetingCorner(corner) ? corner : @"topRight";
    _panelDisplay = [defaults objectForKey:displayKey];
    _policies = NSMutableDictionary.dictionary;
    NSDictionary *savedPolicies = [NSUserDefaults.standardUserDefaults dictionaryForKey:policyKey];
    for (NSString *key in savedPolicies) {
        id entry = savedPolicies[key];
        if (![entry isKindOfClass:NSDictionary.class] || ![entry[@"label"] isKindOfClass:NSString.class] ||
            ![@[@"ask", @"auto", @"never"] containsObject:entry[@"policy"] ?: NSNull.null]) continue;
        if ([key hasPrefix:@"browser:"] && [entry[@"policy"] isEqual:@"auto"]) continue;
        _policies[key] = entry;
    }
    _lastSavedSessionID = [NSUserDefaults.standardUserDefaults stringForKey:lastSessionKey];
    _reminded = NSMutableDictionary.dictionary;
    _calendarSnooze = NSMutableDictionary.dictionary;
    __weak typeof(self) weak = self;
    if (!managed) _calendar = [[JardCalendar alloc] initWithChanged:^{ [weak calendarChanged]; }];
    return self;
}

- (BOOL)busy { return self.recordingID != nil || self.recovering; }
- (BOOL)keepsRunning { return self.busy || self.completionSession || self.detection || (self.calendar.connected && self.calendar.reminders); }

- (BOOL)recording { return [@[@"recording", @"callEnded"] containsObject:self.phase]; }
- (BOOL)hasNewExcerpt {
    if (self.recordingID) return [self.delta unseenTurns:self.liveTurns meeting:self.textMeetingID].count > 0;
    return self.lastSavedSessionID && ![self.exhaustedMeetingID isEqual:self.lastSavedSessionID];
}

- (BOOL)hasExcerpt {
    if (self.recordingID) return self.liveTurns.count > 0;
    return self.lastSavedSessionID && ![self.emptyMeetingID isEqual:self.lastSavedSessionID];
}

- (BOOL)canPaste { return !self.closed && (self.recording || self.hasNewExcerpt); }

- (NSDictionary *)status {
    return @{@"phase": self.phase, @"detection": @(self.detection), @"busy": @(self.busy), @"seconds": @(self.seconds),
        @"provider": self.providerLabel ?: self.offered.label ?: @"", @"managed": @(self.managed),
        @"message": self.managed ? managedMessage : self.message ?: @"",
        @"title": self.meetingTitle ?: self.activeCalendar.title ?: self.provisionalTitle ?: @"",
        @"session": self.sessionID ?: @"", @"hasText": @(self.hasExcerpt),
        @"hasNewText": @(self.hasNewExcerpt), @"copying": @(self.copying), @"policies": self.policies.allValues,
        @"providerPolicies": [self providerPolicies],
        @"microphoneSilent": @(self.recording && self.microphoneSilent),
        @"systemAudioUnavailable": @(self.recording && self.systemAudioUnavailable),
        @"microphone": self.recording ? self.microphoneName ?: @"" : @"",
        @"autoStop": @(self.autoStop), @"autoStopSeconds": @(self.autoStopAt > 0 ? ceil(MAX(0, self.autoStopAt - NSProcessInfo.processInfo.systemUptime)) : 0),
        @"calendar": [self.calendar statusAt:NSDate.date] ?: @{}, @"showIndicator": @(self.showIndicator), @"indicatorCorner": self.panelCorner,
        @"permission": self.permissionSource ?: @"", @"recoveryAvailable": @(self.recoveryDraft != nil)};
}

- (void)state:(NSString *)phase message:(NSString *)message {
    BOOL announce = ![self.phase isEqual:phase] && message.length &&
        [@[@"permission", @"interrupted", @"callEnded", @"finished"] containsObject:phase];
    self.phase = phase;
    self.message = message;
    if (![phase isEqual:@"finished"]) [self clearCompletion];
    [self renderPanel];
    if (announce && (self.caption || NSApp)) NSAccessibilityPostNotificationWithUserInfo((id)self.caption ?: NSApp, NSAccessibilityAnnouncementRequestedNotification,
        @{NSAccessibilityAnnouncementKey: message, NSAccessibilityPriorityKey: @(NSAccessibilityPriorityMedium)});
    if (self.changed) self.changed([self status]);
}

- (void)workspaceReady {
    if (self.workspace) return;
    self.workspace = YES;
    if (!self.managed) {
        __weak typeof(self) weak = self;
        self.timer = [NSTimer timerWithTimeInterval:1 repeats:YES block:^(NSTimer *timer) { [weak poll]; }];
        [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
        [self.calendar refreshAt:NSDate.date];
        [self scanRecovery:NO];
    }
    [self refreshLatestMeeting];
    if (self.changed) self.changed([self status]);
}

- (void)refreshLatestMeeting {
    if (self.closed || self.busy || self.checkingLatest) return;
    self.checkingLatest = YES;
    NSString *previous = self.lastSavedSessionID ?: @"";
    self.send(@"jard.latestMeeting", @{}, ^(id result, NSString *error) {
        self.checkingLatest = NO;
        if (self.closed || error || ![result isKindOfClass:NSDictionary.class]) return;
        self.waitingForRecoveredMeeting = !self.managed && [result[@"recovering"] boolValue];
        self.nextLatestCheck = NSProcessInfo.processInfo.systemUptime + 3;
        if (self.busy || ![previous isEqual:self.lastSavedSessionID ?: @""]) return;
        NSString *session = result[@"session"];
        self.lastSavedSessionID = [session isKindOfClass:NSString.class] && [[NSUUID alloc] initWithUUIDString:session] ? session : nil;
        if (self.lastSavedSessionID) [NSUserDefaults.standardUserDefaults setObject:self.lastSavedSessionID forKey:lastSessionKey];
        else [NSUserDefaults.standardUserDefaults removeObjectForKey:lastSessionKey];
        if (self.changed) self.changed([self status]);
    });
}

- (void)suspendOffers:(BOOL)suspended {
    if (self.suspended == suspended) return;
    self.suspended = suspended;
    self.generation++;
    [self.presence resetObservations];
    if (suspended && self.calendarOffer) [self closeCalendarOffer];
    if (suspended && self.offered && !self.busy) {
        self.offered = nil;
        [self state:@"idle" message:@""];
    }
}

- (void)poll {
    if (self.managed || !self.workspace || self.closed) return;
    [self updateCompletionAt:NSProcessInfo.processInfo.systemUptime pointer:NSEvent.mouseLocation];
    if (self.waitingForRecoveredMeeting && NSProcessInfo.processInfo.systemUptime >= self.nextLatestCheck)
        [self refreshLatestMeeting];
    NSDate *date = NSDate.date;
    [self.calendar refreshAt:date];
    [self pollCalendarAt:date];
    if (!self.detection || self.suspended || self.sampling) return;
    self.sampling = YES;
    NSUInteger generation = self.generation;
    BOOL includeOutput = self.automaticRecording && self.autoStop;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        NSArray *calls = JardActiveCalls(includeOutput);
        dispatch_async(dispatch_get_main_queue(), ^{
            self.sampling = NO;
            if (calls && self.generation == generation && !self.closed && !self.suspended) [self observed:calls];
        });
    });
}

- (void)clearCompletion {
    self.completionGeneration++;
    self.completionSession = nil;
    self.completion = nil;
    self.completionDeadline = 0;
    self.checkingCompletion = NO;
}

- (void)dismissCompletion:(id)sender {
    [self clearCompletion];
    [self renderPanel];
    if (self.changed) self.changed([self status]);
}

- (void)updateCompletionAt:(NSTimeInterval)now pointer:(NSPoint)pointer {
    if (self.closed || !self.completionSession) return;
    if (self.completionDeadline && now >= self.completionDeadline &&
        (!self.panel.visible || !NSPointInRect(pointer, self.panel.frame))) {
        [self dismissCompletion:nil];
        return;
    }
    if (self.checkingCompletion || (self.completionDeadline && ![self.completion[@"summaryStatus"] isEqual:@"queued"] &&
                                   ![self.completion[@"exportStatus"] isEqual:@"pending"])) return;
    self.checkingCompletion = YES;
    NSUInteger generation = self.completionGeneration;
    NSString *session = self.completionSession;
    __weak typeof(self) weak = self;
    self.send(@"jard.meetingCompletion", @{@"session": session}, ^(id result, NSString *error) {
        JardMeetings *owner = weak;
        if (!owner || owner.closed || owner.completionGeneration != generation ||
            ![owner.completionSession isEqual:session]) return;
        owner.checkingCompletion = NO;
        if (error || ![result isKindOfClass:NSDictionary.class]) return;
        NSString *status = result[@"status"];
        if ([status isEqual:@"unavailable"]) {
            [owner dismissCompletion:nil];
            return;
        }
        if (![@[@"processing", @"complete", @"interrupted"] containsObject:status ?: @""]) return;
        owner.completion = result;
        if (!owner.completionDeadline && ![status isEqual:@"processing"]) {
            owner.completionDeadline = NSProcessInfo.processInfo.systemUptime + 15;
            NSAccessibilityPostNotificationWithUserInfo((id)owner.caption ?: NSApp, NSAccessibilityAnnouncementRequestedNotification,
                @{NSAccessibilityAnnouncementKey: @"Meeting transcript is ready.", NSAccessibilityPriorityKey: @(NSAccessibilityPriorityMedium)});
        }
        [owner updateCompletionContent];
    });
}

- (void)observeCalls:(NSArray<JardCall *> *)calls now:(NSTimeInterval)now date:(NSDate *)date {
    JardCall *candidate = [self.presence offerFromCalls:calls now:now];
    if (self.busy) {
        [self observeActiveCalls:calls now:now];
        return;
    }
    if (self.permissionSource || self.recoveryDraft) return;

    if (self.offered) {
        BOOL present = [calls indexOfObjectPassingTest:^BOOL(JardCall *call, NSUInteger i, BOOL *stop) {
            return [call.identity isEqual:self.offered.identity];
        }] != NSNotFound;
        if (present) self.lastPresent = now;
        if (now - self.offeredAt < 20 && now - self.lastPresent < 3) return;
        [self.presence snooze:self.offered absence:now - self.offeredAt >= 20 ? 3 : 1 now:now];
        self.offered = nil;
        [self state:@"idle" message:@""];
        return;
    }
    if (!candidate) return;
    if ([self snoozedCalendarCall:candidate date:date]) {
        [self.presence snooze:candidate absence:3 now:now];
        return;
    }
    NSString *policy = [self policyForCall:candidate];
    if ([policy isEqual:@"never"]) {
        [self.presence snooze:candidate absence:3 now:now];
        return;
    }
    if (self.calendarOffer) {
        if (![policy isEqual:@"auto"] || !JardCalendarMatchesCall(self.calendarOffer, candidate)) return;
        [self closeCalendarOffer];
    }
    self.offered = candidate;
    self.offeredAt = self.lastPresent = now;
    self.providerLabel = nil;
    self.meetingTitle = nil;
    self.provisionalTitle = nil;
    NSString *title = candidate.unnamedBrowser ? [NSString stringWithFormat:@"Call in %@", candidate.label] :
        [NSString stringWithFormat:@"%@ call detected", candidate.label];
    if ([policy isEqual:@"auto"] && !candidate.unnamedBrowser) [self begin:NO automatic:YES languages:@[] reply:nil];
    else [self state:@"offered" message:title];
}

- (void)observed:(NSArray<JardCall *> *)calls {
    [self observeCalls:calls now:NSProcessInfo.processInfo.systemUptime date:NSDate.date];
}

- (void)observeActiveCalls:(NSArray<JardCall *> *)calls now:(NSTimeInterval)now {
    if (!self.activeCall || !self.recording) return;
    BOOL automatic = self.automaticRecording && self.autoStop;
    BOOL present = NO;
    JardCall *other = nil;
    for (JardCall *call in calls) {
        if (!call.inputActive && !automatic) continue;
        if ([call.identity isEqual:self.activeCall.identity] || (call.unnamedBrowser &&
            [call.application isEqual:self.activeCall.application])) present = YES;
        else if (automatic && [self.activeCall.label isEqual:@"Google Meet"] && [call.provider isEqual:self.activeCall.provider] &&
                 [call.application isEqual:self.activeCall.application]) other = call;
    }
    if (present) other = nil;
    if (![self.differentCall isEqual:other.identity]) {
        self.differentCall = other.identity;
        self.differentSince = now;
    }
    if (present || other) {
        self.lastPresent = now;
        self.endPromptAcknowledged = NO;
    }
    if (!other || ![other.identity isEqual:self.keptSwitch]) self.keptSwitch = nil;
    BOOL switched = other && now - self.differentSince >= 4 && !self.keptSwitch;
    NSString *reason = switched ? @"switch" : !present && !other && !self.endPromptAcknowledged &&
        now - self.lastPresent >= 3 ? @"absence" : nil;
    if (!reason) {
        self.autoStopAt = 0;
        self.endedReason = nil;
        if ([self.phase isEqual:@"callEnded"]) [self state:@"recording" message:@""];
        return;
    }
    if (![self.endedReason isEqual:reason]) {
        self.endedReason = reason;
        self.autoStopAt = automatic ? now + 30 : 0;
        NSString *message = automatic ? (switched ? @"A different call opened. Stopping in 30 seconds." :
            @"The call ended. Stopping in 30 seconds.") : @"The call may have ended.";
        [self state:@"callEnded" message:message];
    } else if (self.autoStopAt && now >= self.autoStopAt) {
        [self stop:nil];
    } else if (self.autoStopAt) {
        NSInteger left = (NSInteger)ceil(self.autoStopAt - now);
        self.message = [NSString stringWithFormat:@"%@ Stopping in %ld second%@.", switched ? @"A different call opened." :
            @"The call ended.", (long)left, left == 1 ? @"" : @"s"];
        [self updateCaption];
        if (self.changed) self.changed([self status]);
    }
}

- (NSString *)policyForCall:(JardCall *)call {
    return self.policies[call.provider][@"policy"] ?:
        self.policies[[@"browser:" stringByAppendingString:call.application]][@"policy"] ?:
        (call.unnamedBrowser ? nil : [self policyForLabel:call.label]);
}

- (NSString *)policyForCalendar:(JardCalendarMeeting *)event {
    JardCall *linked = event.link ? JardMeetingBrowserCall(event.link, @"calendar") : nil;
    return linked ? [self policyForCall:linked] : [self policyForLabel:event.provider];
}

- (NSString *)policyForLabel:(NSString *)label {
    if (!label) return nil;
    for (NSDictionary *provider in JardMeetingProviderCatalog()) {
        if (![provider[@"label"] isEqual:label]) continue;
        NSString *policy = self.policies[provider[@"provider"]][@"policy"];
        if (policy) return policy;
        break;
    }
    for (NSDictionary *entry in self.policies.allValues) {
        if ([entry[@"label"] isEqual:label] && [entry[@"policy"] isEqual:@"never"]) return @"never";
    }
    for (NSDictionary *entry in self.policies.allValues) {
        if ([entry[@"label"] isEqual:label] && [entry[@"policy"] isEqual:@"auto"]) return @"auto";
    }
    return nil;
}

- (NSArray<NSDictionary *> *)providerPolicies {
    NSMutableArray<NSDictionary *> *rows = NSMutableArray.array;
    NSMutableSet<NSString *> *builtIn = NSMutableSet.set;
    for (NSDictionary *provider in JardMeetingProviderCatalog()) {
        NSString *key = provider[@"provider"];
        [builtIn addObject:key];
        [rows addObject:@{@"provider": key, @"label": provider[@"label"],
            @"policy": [self policyForLabel:provider[@"label"]] ?: @"ask", @"canAlwaysRecord": @YES}];
    }
    for (NSString *key in [self.policies.allKeys sortedArrayUsingSelector:@selector(localizedCaseInsensitiveCompare:)]) {
        if ([builtIn containsObject:key]) continue;
        NSDictionary *entry = self.policies[key];
        BOOL browser = [key hasPrefix:@"browser:"];
        NSString *scope = [key hasPrefix:@"site:"] ? [key substringFromIndex:5] : @"";
        [rows addObject:@{@"provider": key, @"label": entry[@"label"], @"scope": scope,
            @"policy": entry[@"policy"], @"canAlwaysRecord": @(!browser)}];
    }
    return rows;
}

- (BOOL)snoozedCalendarCall:(JardCall *)call date:(NSDate *)date {
    for (NSString *key in self.calendarSnooze.allKeys) {
        if ([self.calendarSnooze[key] compare:date] != NSOrderedDescending) [self.calendarSnooze removeObjectForKey:key];
    }
    if (self.calendarSnooze[call.label]) return YES;
    if (!self.calendarSnooze[@""]) return NO;
    [self.calendarSnooze removeObjectForKey:@""];
    return YES;
}

- (void)snoozeCalendar:(JardCalendarMeeting *)event {
    NSDate *until = event.provider ? event.end : [event.end earlierDate:[event.start dateByAddingTimeInterval:300]];
    self.calendarSnooze[event.provider ?: @""] = until;
}

- (JardCalendarMeeting *)calendarMeetingForCall:(JardCall *)call date:(NSDate *)now {
    if (!call) return nil;
    for (JardCalendarMeeting *event in self.calendar.meetings) {
        if ([event.start timeIntervalSinceDate:now] <= 300 && [event.end compare:now] == NSOrderedDescending &&
            JardCalendarMatchesCall(event, call)) return event;
    }
    return nil;
}

- (JardCalendarMeeting *)calendarMeeting:(NSString *)identifier date:(NSDate *)now {
    for (JardCalendarMeeting *event in self.calendar.meetings) {
        if ([event.identifier isEqual:identifier] && [event.end compare:now] == NSOrderedDescending) return event;
    }
    return nil;
}

- (void)closeCalendarOffer {
    self.calendarOffer = nil;
    self.reminderExpires = nil;
    if ([self.phase isEqual:@"reminder"]) [self state:@"idle" message:@""];
}

- (void)calendarChanged {
    [self pollCalendarAt:NSDate.date];
    if (self.changed) self.changed([self status]);
}

- (NSString *)calendarMessage:(JardCalendarMeeting *)event date:(NSDate *)now {
    NSInteger mins = (NSInteger)ceil([event.start timeIntervalSinceDate:now] / 60);
    NSString *when = mins > 0 ? [NSString stringWithFormat:@"Starts in %ld min", (long)mins] : @"Starting now";
    NSString *service = self.detection && [[self policyForCalendar:event] isEqual:@"auto"] ? @"Jard records automatically" :
        (event.provider ?: @"");
    return service.length ? [NSString stringWithFormat:@"%@\n%@ · %@", event.title, when, service] :
        [NSString stringWithFormat:@"%@\n%@", event.title, when];
}

- (void)pollCalendarAt:(NSDate *)now {
    if (self.closed || self.suspended || self.busy || self.offered || self.permissionSource || self.recoveryDraft || !self.calendar.reminders) {
        if (self.calendarOffer) [self closeCalendarOffer];
        return;
    }
    for (NSString *key in self.reminded.allKeys) {
        if ([self.reminded[key] compare:now] != NSOrderedDescending) [self.reminded removeObjectForKey:key];
    }
    if (self.calendarOffer) {
        JardCalendarMeeting *event = [self calendarMeeting:self.calendarOffer.identifier date:now];
        if ([self.reminderExpires compare:now] == NSOrderedDescending && event) {
            self.calendarOffer = event;
            NSString *message = [self calendarMessage:event date:now];
            if (![self.message isEqual:message]) [self state:@"reminder" message:message];
            return;
        }
        [self closeCalendarOffer];
    }
    for (JardCalendarMeeting *event in self.calendar.meetings) {
        NSTimeInterval remaining = [event.start timeIntervalSinceDate:now];
        if (remaining > self.calendar.lead * 60 || remaining < -300 || [event.end compare:now] != NSOrderedDescending ||
            self.reminded[event.identifier]) continue;
        self.reminded[event.identifier] = event.end;
        self.calendarOffer = event;
        self.reminderExpires = [[event.start dateByAddingTimeInterval:60] laterDate:[now dateByAddingTimeInterval:30]];
        [self state:@"reminder" message:[self calendarMessage:event date:now]];
        return;
    }
}

- (void)setPolicy:(NSString *)policy provider:(NSString *)provider label:(NSString *)label {
    if (self.policies[provider] || self.policies.count < 80 || [self builtInLabelForProvider:provider]) {
        self.policies[provider] = @{@"provider": provider, @"label": label, @"policy": policy};
    }
    [NSUserDefaults.standardUserDefaults setObject:self.policies forKey:policyKey];
}

- (NSString *)builtInLabelForProvider:(NSString *)key {
    for (NSDictionary *provider in JardMeetingProviderCatalog()) {
        if ([provider[@"provider"] isEqual:key]) return provider[@"label"];
    }
    return nil;
}

- (void)begin:(BOOL)prompt automatic:(BOOL)automatic languages:(NSArray<NSString *> *)languages reply:(JardDictationReply)reply {
    [self begin:prompt automatic:automatic languages:languages calendar:[self calendarMeetingForCall:self.offered date:NSDate.date] reply:reply];
}

- (void)begin:(BOOL)prompt automatic:(BOOL)automatic languages:(NSArray<NSString *> *)languages
      calendar:(JardCalendarMeeting *)event reply:(JardDictationReply)reply {
    [self begin:prompt automatic:automatic languages:languages calendar:event title:nil reply:reply];
}

- (void)begin:(BOOL)prompt automatic:(BOOL)automatic languages:(NSArray<NSString *> *)languages
      calendar:(JardCalendarMeeting *)event title:(NSString *)title reply:(JardDictationReply)reply {
    if (self.managed) { if (reply) reply(nil, managedMessage); return; }
    if (self.closed || self.busy || self.suspended || self.recoveryDraft) {
        if (reply) reply(nil, @"Finish the active capture or update before starting a meeting.");
        return;
    }
    self.activeCall = self.offered;
    self.activeCalendar = event;
    self.activeLanguages = languages;
    self.permissionSource = nil;
    self.permissionCall = nil;
    self.permissionCalendar = nil;
    self.providerLabel = self.activeCall.label ?: event.provider ?: @"Meeting";
    self.meetingTitle = title;
    self.provisionalTitle = nil;
    self.offered = nil;
    if (event) self.reminded[event.identifier] = event.end;
    if (self.calendarOffer) [self closeCalendarOffer];
    [self.liveTurns removeAllObjects];
    self.liveCharacters = 0;
    self.excerptGeneration++;
    self.copying = NO;
    self.excerptFeedback = nil;
    self.seconds = 0;
    self.microphoneSilent = NO;
    self.systemAudioUnavailable = NO;
    self.microphoneName = nil;
    self.automaticRecording = automatic;
    self.autoStopAt = self.differentSince = 0;
    self.differentCall = self.keptSwitch = self.endedReason = nil;
    self.automaticUntil = automatic ? NSProcessInfo.processInfo.systemUptime + 10 : 0;
    self.sessionID = nil;
    self.endPromptAcknowledged = NO;
    self.recordingID = NSUUID.UUID.UUIDString.lowercaseString;
    self.textMeetingID = self.recordingID;
    NSString *identifier = self.recordingID;
    [self state:@"starting" message:automatic ? @"Starting automatically…" : @"Starting meeting recording…"];
    NSMutableDictionary *options = [@{@"meeting": identifier, @"meetingLabel": self.providerLabel, @"languages": languages,
        @"mode": @"both", @"live": @YES, @"diarization": @YES} mutableCopy];
    if (event) options[@"meetingCalendar"] = @{@"title": event.title, @"attendees": event.attendees};
    if (self.meetingTitle) options[@"meetingTitle"] = self.meetingTitle;
    if (self.activeCall.application.length) options[@"meetingApplication"] = self.activeCall.application;
    self.beginCapture(options, prompt, ^(id result, NSString *error) {
        if (self.closed || ![self.recordingID isEqual:identifier]) {
            if (reply) reply(nil, @"The meeting audio helper disconnected.");
            return;
        }
        if (error) {
            NSString *source = [result isKindOfClass:NSDictionary.class] ? result[@"permission"] : nil;
            if ([@[@"mic", @"system"] containsObject:source ?: @""]) [self blockForPermission:source];
            else if (self.activeCall && !self.suspended) {
                self.recordingID = nil;
                self.offered = self.activeCall;
                self.activeCall = nil;
                self.offeredAt = self.lastPresent = NSProcessInfo.processInfo.systemUptime;
                [self state:@"offered" message:error];
            } else {
                [self finish:error session:nil];
            }
        }
        if (reply) reply(error ? nil : [self status], error);
    });
}

- (void)finish:(NSString *)error session:(NSString *)session {
    NSTimeInterval now = NSProcessInfo.processInfo.systemUptime;
    if (self.activeCall && now - self.lastPresent < 3) [self.presence snooze:self.activeCall absence:3 now:self.lastPresent];
    [self clearCompletion];
    if (self.activeCalendar.title.length) self.provisionalTitle = self.activeCalendar.title;
    self.activeCall = nil;
    self.activeCalendar = nil;
    self.automaticRecording = NO;
    self.autoStopAt = 0;
    NSString *active = self.recordingID ? self.textMeetingID : nil;
    self.recordingID = nil;
    self.sessionID = session;
    if ([session isKindOfClass:NSString.class] && [[NSUUID alloc] initWithUUIDString:session]) {
        [self.delta replaceMeeting:active with:session];
        if (![self.textMeetingID isEqual:session] && !active) {
            [self.liveTurns removeAllObjects];
            self.liveCharacters = 0;
        }
        self.textMeetingID = session;
        self.lastSavedSessionID = session;
        [NSUserDefaults.standardUserDefaults setObject:session forKey:lastSessionKey];
        if (!error) self.completionSession = session;
    }
    [self state:error ? @"error" : @"finished" message:error ?: @"Meeting saved to Sessions."];
    [self updateCompletionAt:NSProcessInfo.processInfo.systemUptime pointer:NSEvent.mouseLocation];
}

- (void)handleCapture:(NSDictionary *)capture {
    if (!self.recordingID || ![capture[@"meeting"] isEqual:self.recordingID]) return;
    NSString *event = capture[@"event"];
    if ([event isEqual:@"started"]) {
        self.microphoneName = [capture[@"microphone"] isKindOfClass:NSString.class] ? capture[@"microphone"] : nil;
        NSString *session = capture[@"session"];
        if ([session isKindOfClass:NSString.class] && [[NSUUID alloc] initWithUUIDString:session]) {
            [self.delta replaceMeeting:self.textMeetingID with:session];
            self.textMeetingID = session;
        }
        self.lastPresent = NSProcessInfo.processInfo.systemUptime;
        [self state:@"recording" message:@""];
    } else if ([event isEqual:@"meetingTitle"] && self.recording && !self.meetingTitle) {
        NSString *title = [capture[@"title"] isKindOfClass:NSString.class] ? capture[@"title"] : nil;
        if (!title.length || title.length > 240) return;
        self.provisionalTitle = title;
        [self updateCaption];
        if (self.changed) self.changed([self status]);
    } else if ([event isEqual:@"meetingMicWarning"] && self.recording) {
        self.microphoneSilent = [capture[@"active"] boolValue];
        [self updateCaption];
        if (self.changed) self.changed([self status]);
    } else if ([event isEqual:@"meetingSystemAudio"] && self.recording) {
        BOOL unavailable = ![capture[@"available"] boolValue];
        if (unavailable == self.systemAudioUnavailable) return;
        self.systemAudioUnavailable = unavailable;
        [self updateCaption];
        if (self.changed) self.changed([self status]);
    } else if ([event isEqual:@"sourceChanged"] && self.recording) {
        NSString *name = [capture[@"microphone"] isKindOfClass:NSString.class] ? capture[@"microphone"] : nil;
        if (!name.length || [self.microphoneName isEqual:name]) return;
        self.microphoneName = name;
        if (self.changed) self.changed([self status]);
    } else if ([event isEqual:@"levels"]) {
        NSInteger before = (NSInteger)self.seconds;
        self.seconds = [capture[@"seconds"] doubleValue];
        [self updateCaption];
        if ((NSInteger)self.seconds != before && self.changed) self.changed([self status]);
    } else if ([event isEqual:@"preview"]) {
        NSArray<NSDictionary *> *turns = JardMeetingTurns(capture[@"deltaSegments"]);
        if (!turns.count && [capture[@"delta"] isKindOfClass:NSString.class])
            turns = JardMeetingTurns(@[@{@"text": capture[@"delta"]}]);
        if (!turns.count) return;
        NSNumber *frame = [capture[@"through"] isKindOfClass:NSNumber.class] ? capture[@"through"] : nil;
        for (NSDictionary *turn in turns) {
            NSMutableDictionary *entry = turn.mutableCopy;
            if (frame) entry[@"through"] = frame;
            [self.liveTurns addObject:entry];
            self.liveCharacters += [turn[@"text"] length];
        }
        NSUInteger remove = 0;
        while (self.liveCharacters > 500000 && remove < self.liveTurns.count)
            self.liveCharacters -= [self.liveTurns[remove++][@"text"] length];
        if (remove) [self.liveTurns removeObjectsInRange:NSMakeRange(0, remove)];
        [self updateCaption];
        [self updateCopyControls];
        if (self.changed) self.changed([self status]);
    } else if ([event isEqual:@"finished"]) {
        [self state:@"saving" message:@"Saving and transcribing the full meeting…"];
    } else if ([event isEqual:@"accepted"]) {
        [self finish:nil session:capture[@"id"]];
        if ([capture[@"interruption"] isKindOfClass:NSString.class]) [self state:@"finished" message:@"Saved the audio captured before the recording was interrupted."];
    } else if ([event isEqual:@"error"]) {
        NSString *source = capture[@"permission"], *draft = capture[@"draft"];
        if ([@[@"mic", @"system"] containsObject:source ?: @""]) [self blockForPermission:source];
        else if ([draft isKindOfClass:NSString.class] && [[NSUUID alloc] initWithUUIDString:draft]) [self showRecovery:draft];
        else [self finish:capture[@"message"] ?: @"The meeting could not be saved." session:nil];
    }
}

- (void)blockForPermission:(NSString *)source {
    self.permissionSource = source;
    self.permissionCall = self.activeCall;
    self.permissionCalendar = self.activeCalendar;
    self.recordingID = nil;
    self.activeCall = nil;
    self.activeCalendar = nil;
    self.automaticRecording = NO;
    self.autoStopAt = 0;
    [self state:@"permission" message:[source isEqual:@"mic"] ?
        @"Allow Microphone access to record this meeting." : @"Allow Mac audio access to record this meeting."];
}

- (void)retryPermission:(JardDictationReply)reply {
    if (!self.permissionSource) { if (reply) reply(nil, @"No recording is waiting for permission."); return; }
    self.offered = self.permissionCall;
    [self begin:YES automatic:NO languages:self.activeLanguages ?: @[] calendar:self.permissionCalendar title:self.meetingTitle reply:reply];
}

- (void)dismissPermission {
    if (self.permissionCall) [self.presence snooze:self.permissionCall absence:60 now:NSProcessInfo.processInfo.systemUptime];
    self.permissionSource = nil;
    self.permissionCall = nil;
    self.permissionCalendar = nil;
    self.meetingTitle = nil;
    [self state:@"idle" message:@""];
}

- (void)showRecovery:(NSString *)draft {
    self.activeCall = nil;
    self.activeCalendar = nil;
    self.automaticRecording = NO;
    self.autoStopAt = 0;
    self.recordingID = nil;
    self.recoveryDraft = draft;
    self.sessionID = nil;
    [self state:@"interrupted" message:@"Meeting interrupted. Recover the recorded audio."];
}

- (void)scanRecovery:(BOOL)showResult {
    if (self.managed || self.closed || self.busy || self.suspended || self.scanningRecovery) return;
    self.scanningRecovery = YES;
    self.send(@"jard.meetingDrafts", @{}, ^(id result, NSString *error) {
        self.scanningRecovery = NO;
        if (self.closed || self.busy || self.suspended || self.offered || self.calendarOffer || self.permissionSource) return;
        if (error) {
            if (showResult) [self state:self.phase message:@"Could not check for interrupted meetings."];
            return;
        }
        NSArray *drafts = [result isKindOfClass:NSArray.class] ? result : @[];
        NSDictionary *first = [drafts.firstObject isKindOfClass:NSDictionary.class] ? drafts.firstObject : nil;
        NSString *identifier = first[@"id"];
        if ([identifier isKindOfClass:NSString.class] && [[NSUUID alloc] initWithUUIDString:identifier]) {
            self.providerLabel = [first[@"provider"] isKindOfClass:NSString.class] ? first[@"provider"] : @"Meeting";
            self.meetingTitle = [first[@"title"] isKindOfClass:NSString.class] ? first[@"title"] : nil;
            self.provisionalTitle = nil;
            [self showRecovery:identifier];
        } else if (self.recoveryDraft) {
            self.recoveryDraft = nil;
            self.meetingTitle = nil;
            [self state:@"idle" message:@""];
        } else if (showResult) [self state:self.phase message:@"No interrupted meetings found."];
    });
}

- (void)recoverMeeting:(JardDictationReply)reply {
    if (self.managed) { if (reply) reply(nil, managedMessage); return; }
    if (!self.recoveryDraft || self.busy || self.suspended) {
        if (reply) reply(nil, @"The meeting recording is unavailable for recovery.");
        return;
    }
    NSString *draft = self.recoveryDraft;
    self.recovering = YES;
    [self state:@"saving" message:@"Recovering recorded audio…"];
    self.send(@"jard.recoverMeeting", @{@"id": draft}, ^(id result, NSString *error) {
        self.recovering = NO;
        if (self.closed) return;
        if (error || ![result[@"id"] isEqual:draft]) {
            NSString *failure = error ?: @"The meeting could not be recovered.";
            [self state:@"interrupted" message:failure];
            if (reply) reply(nil, failure);
            return;
        }
        self.recoveryDraft = nil;
        [self finish:nil session:draft];
        [self state:@"finished" message:@"Recovered meeting saved to Sessions."];
        if (reply) reply([self status], nil);
    });
}

- (void)dismissRecovery {
    if (self.recovering) return;
    self.recoveryDraft = nil;
    [self state:@"idle" message:@""];
}

- (void)stop:(JardDictationReply)reply {
    if (!self.recording) {
        if (reply) reply(nil, @"No meeting is ready to stop.");
        return;
    }
    NSString *identifier = self.recordingID;
    self.autoStopAt = 0;
    [self state:@"saving" message:@"Saving the meeting…"];
    self.send(@"jard.stopMeeting", @{@"meeting": identifier}, ^(id result, NSString *error) {
        if (error && [self.recordingID isEqual:identifier]) [self finish:error session:nil];
        if (reply) reply(error ? nil : [self status], error);
    });
}

- (NSArray<NSDictionary *> *)liveTurnsThrough:(NSNumber *)frame {
    if (!frame) return self.liveTurns.copy;
    NSMutableArray *result = NSMutableArray.array;
    for (NSDictionary *turn in self.liveTurns) {
        NSNumber *end = turn[@"end"] == NSNull.null ? turn[@"through"] : @([turn[@"end"] doubleValue] * 48000);
        if (end && end.doubleValue <= frame.doubleValue + 0.01) [result addObject:turn];
    }
    return result;
}

- (void)prepareExcerpt:(BOOL)onlyNew savedSession:(NSString *)savedSession ready:(void (^)(NSString *, NSString *, void (^)(BOOL)))ready {
    if (self.copying) { ready(nil, @"Jard is already preparing a meeting excerpt.", ^(BOOL success) {}); return; }
    NSString *active = savedSession ? nil : self.recordingID;
    NSString *identifier = active ? self.textMeetingID : savedSession ?: self.lastSavedSessionID;
    if (!identifier || self.closed) { ready(nil, @"No meeting transcript is available.", ^(BOOL success) {}); return; }
    self.copying = YES;
    NSUInteger generation = ++self.excerptGeneration;
    [self updateCopyControls];
    if (self.changed) self.changed([self status]);
    JardDictationReply received = ^(id result, NSString *error) {
        if (self.closed || self.excerptGeneration != generation) {
            ready(nil, @"The meeting changed before the excerpt was ready.", ^(BOOL success) {});
            return;
        }
        NSDictionary *snapshot = [result isKindOfClass:NSDictionary.class] ? result : @{};
        NSNumber *through = [snapshot[@"through"] isKindOfClass:NSNumber.class] ? snapshot[@"through"] : nil;
        BOOL settled = !active && [snapshot[@"settled"] boolValue];
        NSString *meeting = active ? (self.textMeetingID ?: identifier) : identifier;
        NSArray *turns = active ? [self liveTurnsThrough:through] : JardMeetingTurns(snapshot[@"turns"]);
        if (!active && !settled && !error && !turns.count && [self.textMeetingID isEqual:identifier])
            turns = self.liveTurns.copy;
        NSArray *selected = onlyNew ? [self.delta unseenTurns:turns meeting:meeting] : turns;
        NSDictionary *labels = [snapshot[@"labels"] isKindOfClass:NSDictionary.class] ? snapshot[@"labels"] : nil;
        NSString *text = JardMeetingExcerptText(selected, labels);
        if (settled && !turns.count) self.emptyMeetingID = meeting;
        if (settled && !selected.count) self.exhaustedMeetingID = meeting;
        NSString *notice = active && (error || (through && ![snapshot[@"complete"] boolValue]))
            ? @"Copied the transcript available so far." : nil;
        ready(text, text.length ? notice : error ?: @"No new meeting text is available yet.", ^(BOOL success) {
            if (self.excerptGeneration != generation) return;
            if (success && selected.count) {
                [self.delta deliveredTurns:selected meeting:active ? (self.textMeetingID ?: meeting) : meeting];
                if (settled) self.exhaustedMeetingID = meeting;
            }
            self.copying = NO;
            [self updateCopyControls];
            if (self.changed) self.changed([self status]);
        });
    };
    if (self.recording) self.send(@"jard.copyMeeting", @{@"meeting": active}, received);
    else if (active) received(nil, nil);
    else self.send(@"jard.meetingExcerpt", @{@"session": identifier}, received);
}

- (void)copyNew:(BOOL)onlyNew to:(NSPasteboard *)board reply:(JardDictationReply)reply {
    [self prepareExcerpt:onlyNew savedSession:nil ready:^(NSString *text, NSString *notice, void (^didCopy)(BOOL)) {
        BOOL copied = NO;
        if (text.length) {
            [board clearContents];
            copied = [board setString:text forType:NSPasteboardTypeString];
        }
        didCopy(copied);
        if (!reply) [self confirmCopy:copied ? notice ?: @"Meeting transcript copied." : notice ?: @"No meeting text to copy yet."
            brief:copied ? @"Copied" : @"No text yet"];
        if (reply) {
            NSMutableDictionary *result = copied ? [[self status] mutableCopy] : nil;
            if (result && notice) result[@"copyPartial"] = @YES;
            reply(result, copied ? nil : notice ?: @"Could not copy the meeting excerpt.");
        }
    }];
}

- (void)preparePaste:(JardPasteExcerpt)ready {
    [self prepareExcerpt:YES savedSession:nil ready:^(NSString *text, NSString *notice, void (^didPaste)(BOOL)) {
        ready(text, didPaste);
    }];
}

- (void)dismissOffer {
    if (self.offered) [self.presence snooze:self.offered absence:60 now:NSProcessInfo.processInfo.systemUptime];
    self.offered = nil;
    [self state:@"idle" message:@""];
}

- (void)request:(NSDictionary *)request reply:(JardDictationReply)reply {
    NSString *action = request[@"action"];
    if (self.managed && ![action isEqual:@"status"] && ![action isEqual:@"copyNew"] && ![action isEqual:@"copyAll"]) {
        reply(nil, managedMessage);
        return;
    }
    if ([action isEqual:@"start"]) {
        NSArray *languages = request[@"languages"] ?: @[];
        if (![languages isKindOfClass:NSArray.class] || languages.count > 25) {
            reply(nil, @"Invalid meeting language hints."); return;
        }
        for (id language in languages) {
            if (![language isKindOfClass:NSString.class] || [language length] > 12) {
                reply(nil, @"Invalid meeting language hints."); return;
            }
        }
        [self begin:YES automatic:NO languages:languages reply:reply];
        return;
    } else if ([action isEqual:@"stop"]) {
        [self stop:reply];
        return;
    } else if ([action isEqual:@"indicator"]) {
        NSNumber *enabled = request[@"enabled"];
        if (enabled != (__bridge NSNumber *)kCFBooleanTrue && enabled != (__bridge NSNumber *)kCFBooleanFalse) {
            reply(nil, @"Choose whether to show the recording indicator."); return;
        }
        self.showIndicator = enabled.boolValue;
        [NSUserDefaults.standardUserDefaults setBool:self.showIndicator forKey:indicatorKey];
        [self renderPanel];
        if (self.changed) self.changed([self status]);
    } else if ([action isEqual:@"indicatorCorner"]) {
        NSString *corner = request[@"corner"];
        if (![corner isKindOfClass:NSString.class] || !JardValidMeetingCorner(corner)) {
            reply(nil, @"Choose a corner for the recording indicator."); return;
        }
        NSScreen *screen = self.panel.screen ?: [self indicatorScreen];
        [self saveCorner:corner screen:screen];
        [self.panel moveToCorner:corner screen:screen animated:YES];
    } else if ([action isEqual:@"retryPermission"]) {
        [self retryPermission:reply];
        return;
    } else if ([action isEqual:@"recordingPrivacy"]) {
        [self openRecordingPrivacy];
    } else if ([action isEqual:@"dismissPermission"]) {
        if (self.permissionSource) [self dismissPermission];
    } else if ([action isEqual:@"recoverMeeting"]) {
        [self recoverMeeting:reply];
        return;
    } else if ([action isEqual:@"dismissRecovery"]) {
        if (self.recoveryDraft) [self dismissRecovery];
    } else if ([action isEqual:@"refreshRecovery"]) {
        [self scanRecovery:YES];
    } else if ([action isEqual:@"keepRecording"]) {
        if ([self.phase isEqual:@"callEnded"]) [self keepButton:nil];
    } else if ([action isEqual:@"calendarConnect"]) {
        [self.calendar connect:^(NSString *error) { reply(error ? nil : [self status], error); }];
        return;
    } else if ([action isEqual:@"calendarDisconnect"]) {
        [self.calendar disconnect];
        [self.calendarSnooze removeAllObjects];
        [self.reminded removeAllObjects];
    } else if ([action isEqual:@"calendarSettings"]) {
        [NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:@"x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars"]];
    } else if ([action isEqual:@"calendarLead"]) {
        NSNumber *minutes = request[@"minutes"];
        if (![minutes isKindOfClass:NSNumber.class] || ![@[@0, @1, @2, @5] containsObject:minutes]) {
            reply(nil, @"Choose a calendar reminder time."); return;
        }
        [self.calendar setLead:minutes.integerValue];
    } else if ([action isEqual:@"calendarEvent"]) {
        NSString *identifier = request[@"id"], *choice = request[@"choice"];
        if (![identifier isKindOfClass:NSString.class] || ![choice isKindOfClass:NSString.class]) {
            reply(nil, @"Choose an upcoming meeting."); return;
        }
        [self answerCalendar:identifier choice:choice reply:reply];
        return;
    } else if ([action isEqual:@"calendarReminders"] || [action isEqual:@"calendarInclude"] || [action isEqual:@"autoStop"]) {
        NSNumber *enabled = request[@"enabled"];
        if (enabled != (__bridge NSNumber *)kCFBooleanTrue && enabled != (__bridge NSNumber *)kCFBooleanFalse) {
            reply(nil, @"Choose whether to enable this meeting setting."); return;
        }
        if ([action isEqual:@"calendarReminders"]) [self.calendar setReminders:enabled.boolValue];
        else if ([action isEqual:@"calendarInclude"]) {
            NSString *identifier = request[@"id"];
            if (![identifier isKindOfClass:NSString.class] || ![self.calendar setIncluded:enabled.boolValue calendar:identifier]) {
                reply(nil, @"This calendar is unavailable."); return;
            }
        } else {
            self.autoStop = enabled.boolValue;
            [NSUserDefaults.standardUserDefaults setBool:self.autoStop forKey:autoStopKey];
            self.generation++;
            if (!self.autoStop && self.autoStopAt) [self keepButton:nil];
        }
    } else if ([action isEqual:@"detection"]) {
        NSNumber *enabled = request[@"enabled"];
        if (enabled != (__bridge NSNumber *)kCFBooleanTrue && enabled != (__bridge NSNumber *)kCFBooleanFalse) {
            reply(nil, @"Expected a meeting detection setting."); return;
        }
        self.detection = enabled.boolValue;
        [NSUserDefaults.standardUserDefaults setBool:self.detection forKey:detectionKey];
        self.generation++;
        [self.presence resetObservations];
        if (!self.detection && self.offered) [self dismissOffer];
        if (!self.detection && [self.phase isEqual:@"callEnded"]) [self keepButton:nil];
    } else if ([action isEqual:@"copyAll"] || [action isEqual:@"copyNew"]) {
        [self copyNew:[action isEqual:@"copyNew"] to:NSPasteboard.generalPasteboard reply:reply];
        return;
    } else if ([action isEqual:@"setPolicy"]) {
        NSString *provider = request[@"provider"];
        NSString *policy = request[@"policy"];
        NSString *label = [provider isKindOfClass:NSString.class] ?
            [self builtInLabelForProvider:provider] ?: self.policies[provider][@"label"] : nil;
        if (!label.length || ![@[@"ask", @"auto", @"never"] containsObject:policy ?: NSNull.null] ||
            ([policy isEqual:@"auto"] && [provider hasPrefix:@"browser:"])) {
            reply(nil, @"Choose an available recording preference for this provider."); return;
        }
        [self setPolicy:policy provider:provider label:label];
        if (self.offered && [[self policyForCall:self.offered] isEqual:@"never"]) [self dismissOffer];
        else {
            if (self.calendarOffer) [self renderPanel];
            if (self.changed) self.changed([self status]);
        }
    } else if (![action isEqual:@"status"]) {
        reply(nil, @"Unknown meeting action."); return;
    }
    reply([self status], nil);
}

- (void)startWithEstablishedPermissionsTitle:(NSString *)title reply:(JardDictationReply)reply {
    JardCalendarMeeting *calendar = [self calendarMeetingForCall:self.offered date:NSDate.date];
    [self begin:NO automatic:NO languages:@[] calendar:calendar title:title reply:reply];
}

- (void)copyLatestTranscript:(JardDictationReply)reply {
    if (!self.lastSavedSessionID) {
        reply(nil, @"Record and save a meeting before copying its final transcript.");
        return;
    }
    self.send(@"jard.meetingTranscript", @{@"session": self.lastSavedSessionID}, ^(id result, NSString *error) {
        if (error) { reply(nil, error); return; }
        NSPasteboard *board = NSPasteboard.generalPasteboard;
        [board clearContents];
        if (![board setString:result[@"labeledText"] ?: result[@"text"] ?: @"" forType:NSPasteboardTypeString]) {
            reply(nil, @"Could not write the meeting transcript to the clipboard.");
            return;
        }
        reply(@{@"copied": @YES}, nil);
    });
}

- (void)bridgeDisconnected {
    if (!self.recordingID) return;
    [self finish:@"The recorder disconnected. Checking for recorded audio…" session:nil];
    __weak typeof(self) weak = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 500 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{ [weak scanRecovery:YES]; });
}

- (void)recordButton:(id)sender { [self begin:YES automatic:NO languages:@[] reply:nil]; }
- (void)dismissButton:(id)sender { [self dismissOffer]; }
- (void)copyAllButton:(id)sender { [self copyNew:NO to:NSPasteboard.generalPasteboard reply:nil]; }
- (void)copyNewButton:(id)sender { [self copyNew:YES to:NSPasteboard.generalPasteboard reply:nil]; }
- (void)copyCompletedMeeting:(BOOL)onlyNew to:(NSPasteboard *)board {
    NSString *session = self.completionSession;
    if (!session || !self.completionDeadline || ![self.completion[@"hasSpeech"] boolValue]) return;
    NSInteger before = board.changeCount;
    [self prepareExcerpt:onlyNew savedSession:session ready:^(NSString *text, NSString *notice, void (^didCopy)(BOOL)) {
        void (^copy)(NSString *) = ^(NSString *value) {
            BOOL fresh = !self.closed && [self.sessionID isEqual:session] && board.changeCount == before;
            BOOL copied = NO;
            if (fresh && value.length) {
                [board clearContents];
                copied = [board setString:value forType:NSPasteboardTypeString];
            }
            didCopy(copied);
            if (self.closed || ![self.sessionID isEqual:session]) return;
            [self confirmCopy:copied ? @"Meeting transcript copied." : notice ?: @"No meeting text was copied."
                         brief:copied ? @"Copied" : @"No text copied"];
        };
        if (onlyNew || !text.length || ![self.sessionID isEqual:session]) { copy(text); return; }
        self.send(@"jard.meetingMarkdown", @{@"session": session}, ^(id result, NSString *error) {
            NSString *markdown = [result isKindOfClass:NSDictionary.class] &&
                [result[@"text"] isKindOfClass:NSString.class] ? result[@"text"] : nil;
            copy(markdown.length ? markdown : text);
        });
    }];
}
- (void)copyCompletedNewButton:(id)sender { [self copyCompletedMeeting:YES to:NSPasteboard.generalPasteboard]; }
- (void)copyCompletedAllButton:(id)sender { [self copyCompletedMeeting:NO to:NSPasteboard.generalPasteboard]; }
- (void)copyMeetingAgentPrompt:(id)sender { [self copyMeetingAgentPromptTo:NSPasteboard.generalPasteboard]; }
- (void)copyMeetingAgentPromptTo:(NSPasteboard *)board {
    if (!self.completionSession || !self.completionDeadline) return;
    NSString *prompt = [NSString stringWithFormat:@"Read this saved Jard meeting locally to answer my questions about it.\n\njardctl meetings show %@ --md",
        self.completionSession];
    [board clearContents];
    if ([board setString:prompt forType:NSPasteboardTypeString]) {
        NSAccessibilityPostNotificationWithUserInfo((id)self.caption ?: NSApp, NSAccessibilityAnnouncementRequestedNotification,
            @{NSAccessibilityAnnouncementKey: @"Meeting agent prompt copied.", NSAccessibilityPriorityKey: @(NSAccessibilityPriorityMedium)});
    }
}
- (void)stopButton:(id)sender { [self stop:nil]; }
- (void)retryPermissionButton:(id)sender { [self retryPermission:nil]; }
- (void)privacyButton:(id)sender { [self openRecordingPrivacy]; }
- (void)dismissPermissionButton:(id)sender { [self dismissPermission]; }
- (void)recoverButton:(id)sender { [self recoverMeeting:nil]; }
- (void)dismissRecoveryButton:(id)sender { [self dismissRecovery]; }
- (void)keepButton:(id)sender {
    if ([self.endedReason isEqual:@"switch"]) self.keptSwitch = self.differentCall;
    self.endPromptAcknowledged = YES;
    self.endedReason = nil;
    self.autoStopAt = 0;
    [self state:@"recording" message:@""];
}

- (BOOL)openMeetingLink:(NSURL *)url { return [NSWorkspace.sharedWorkspace openURL:url]; }

- (void)answerCalendar:(NSString *)identifier choice:(NSString *)choice reply:(JardDictationReply)reply {
    void (^respond)(id, NSString *) = reply ?: ^(id result, NSString *error) {
        if (error) [self state:self.phase message:error];
    };
    JardCalendarMeeting *event = [self calendarMeeting:identifier date:NSDate.date];
    if (!event || ![@[@"join", @"record", @"joinRecord", @"dismiss"] containsObject:choice]) {
        respond(nil, @"This calendar meeting is no longer available."); return;
    }
    BOOL joining = [@[@"join", @"joinRecord"] containsObject:choice];
    BOOL record = [@[@"record", @"joinRecord"] containsObject:choice];
    if (record && (self.busy || self.suspended)) {
        respond(nil, @"Finish the active capture before recording another meeting."); return;
    }
    if (joining && (!event.link || ![self openMeetingLink:event.link])) {
        respond(nil, @"Could not open the calendar call link."); return;
    }
    self.reminded[event.identifier] = event.end;
    if ([choice isEqual:@"dismiss"] || record) [self snoozeCalendar:event];
    if ([self.calendarOffer.identifier isEqual:event.identifier]) [self closeCalendarOffer];
    if (record) {
        // A simultaneous detected-call offer can refer to another meeting.
        self.offered = nil;
        [self begin:YES automatic:NO languages:@[] calendar:event reply:respond];
    } else respond([self status], nil);
}

- (void)calendarJoin:(id)sender { [self answerCalendar:self.calendarOffer.identifier choice:@"join" reply:nil]; }
- (void)calendarRecord:(id)sender { [self answerCalendar:self.calendarOffer.identifier choice:@"record" reply:nil]; }
- (void)calendarJoinRecord:(id)sender { [self answerCalendar:self.calendarOffer.identifier choice:@"joinRecord" reply:nil]; }
- (void)calendarDismiss:(id)sender { [self answerCalendar:self.calendarOffer.identifier choice:@"dismiss" reply:nil]; }
- (void)alwaysButton:(id)sender {
    JardCall *call = self.offered;
    if (!call) return;
    [self setPolicy:@"auto" provider:call.provider label:call.label];
    [self begin:YES automatic:NO languages:@[] reply:nil];
}
- (void)neverButton:(id)sender {
    JardCall *call = self.offered;
    if (!call) return;
    [self setPolicy:@"never" provider:call.provider label:call.label];
    [self dismissOffer];
}

- (NSButton *)button:(NSString *)title action:(SEL)action area:(NSRect)area into:(NSView *)container {
    NSButton *button = [NSButton buttonWithTitle:title target:self action:action];
    button.frame = area;
    [button setAccessibilityLabel:title];
    [container addSubview:button];
    return button;
}

- (void)openRecordingPrivacy {
    if (!self.permissionSource) return;
    if ([self.permissionSource isEqual:@"system"] && self.showSystemAudioSettings) {
        self.showSystemAudioSettings();
        return;
    }
    NSString *pane = [self.permissionSource isEqual:@"mic"] ? @"Privacy_Microphone" : @"Privacy_ScreenCapture";
    [NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:[@"x-apple.systempreferences:com.apple.preference.security?" stringByAppendingString:pane]]];
}

- (NSScreen *)indicatorScreen {
    NSScreen *underPointer = nil;
    NSPoint cursor = NSEvent.mouseLocation;
    for (NSScreen *screen in NSScreen.screens) {
        if ([screen.deviceDescription[@"NSScreenNumber"] isEqual:self.panelDisplay]) return screen;
        if (NSPointInRect(cursor, screen.frame)) underPointer = screen;
    }
    return underPointer ?: NSScreen.mainScreen;
}

- (void)saveCorner:(NSString *)corner screen:(NSScreen *)screen {
    self.panelCorner = corner;
    self.panelDisplay = screen.deviceDescription[@"NSScreenNumber"];
    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    [defaults setObject:corner forKey:cornerKey];
    [defaults setObject:self.panelDisplay forKey:displayKey];
    if (self.changed) self.changed([self status]);
}

- (void)confirmCopy:(NSString *)message brief:(NSString *)brief {
    self.excerptFeedback = brief;
    [self updateCopyControls];
    if (self.caption || NSApp) NSAccessibilityPostNotificationWithUserInfo((id)self.caption ?: NSApp,
        NSAccessibilityAnnouncementRequestedNotification,
        @{NSAccessibilityAnnouncementKey: message, NSAccessibilityPriorityKey: @(NSAccessibilityPriorityMedium)});
    NSUInteger generation = ++self.excerptFeedbackGeneration;
    __weak typeof(self) weak = self;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 1500 * NSEC_PER_MSEC), dispatch_get_main_queue(), ^{
        if (weak.excerptFeedbackGeneration != generation) return;
        weak.excerptFeedback = nil;
        [weak updateCopyControls];
    });
}

- (void)updateCaption {
    if (!self.recording || !self.caption) return;
    long elapsed = (long)MAX(0, self.seconds);
    NSString *clock = [NSString stringWithFormat:@"%ld:%02ld", elapsed / 60, elapsed % 60];
    BOOL ended = [self.phase isEqual:@"callEnded"];
    self.caption.maximumNumberOfLines = ended ? 2 : 1;
    self.caption.lineBreakMode = ended ? NSLineBreakByWordWrapping : NSLineBreakByTruncatingTail;
    if (ended) self.caption.stringValue = self.message;
    else {
        NSString *label = self.systemAudioUnavailable ? @"Mac audio reconnecting" : self.microphoneSilent ? @"Microphone silent" :
            NSProcessInfo.processInfo.systemUptime < self.automaticUntil ? @"Started automatically" :
            self.meetingTitle ?: self.activeCalendar.title ?: self.provisionalTitle ?: self.providerLabel;
        NSString *caption = [NSString stringWithFormat:@"%@ · %@", label, clock];
        if ([caption sizeWithAttributes:@{NSFontAttributeName: self.caption.font}].width > NSWidth(self.caption.bounds) - 6)
            caption = [NSString stringWithFormat:@"%@ · %@", clock, label];
        self.caption.stringValue = caption;
    }
    [self.caption setAccessibilityLabel:[@"Meeting recording. " stringByAppendingString:self.caption.stringValue]];
}

- (void)updateCopyControls {
    BOOL available = !self.completionSession || (self.completionDeadline && [self.completion[@"hasSpeech"] boolValue]);
    BOOL unused = !self.completionSession || ![self.exhaustedMeetingID isEqual:self.completionSession];
    self.incrementalTextButton.enabled = !self.copying && available && unused;
    self.fullTextButton.enabled = !self.copying && available;
    self.agentPromptButton.enabled = self.completionDeadline > 0;
    self.incrementalTextButton.title = self.copying ? @"Preparing…" : self.excerptFeedback ?: @"Copy New";
    [self.incrementalTextButton setAccessibilityLabel:self.copying ? @"Preparing new meeting transcript" : @"Copy new meeting transcript"];
    [self.fullTextButton setAccessibilityLabel:@"Copy entire meeting transcript"];
}

- (void)updateCompletionContent {
    if (!self.completionSession || !self.completionBody) return;
    NSString *title = [self.completion[@"title"] isKindOfClass:NSString.class] ? self.completion[@"title"] :
        self.meetingTitle ?: self.provisionalTitle ?: self.providerLabel ?: @"Meeting";
    NSInteger seconds = [self.completion[@"duration"] isKindOfClass:NSNumber.class] ?
        [self.completion[@"duration"] integerValue] : (NSInteger)self.seconds;
    self.caption.stringValue = [NSString stringWithFormat:@"%ld:%02ld · %@", (long)seconds / 60, (long)seconds % 60, title];
    self.caption.accessibilityLabel = self.caption.stringValue;
    NSString *summary = [self.completion[@"summary"] isKindOfClass:NSString.class] ? self.completion[@"summary"] : nil;
    NSString *error = [self.completion[@"error"] isKindOfClass:NSString.class] ? self.completion[@"error"] : nil;
    if (!self.completionDeadline) self.completionBody.stringValue = @"Transcribing the saved meeting…";
    else if ([self.completion[@"exportStatus"] isEqual:@"error"]) self.completionBody.stringValue = @"Markdown export failed. See the meeting in Sessions.";
    else if (summary.length) self.completionBody.stringValue = summary;
    else if (error.length) self.completionBody.stringValue = error;
    else if ([self.completion[@"summaryStatus"] isEqual:@"queued"]) self.completionBody.stringValue = @"Writing a title and summary with local Qwen…";
    else if ([self.completion[@"summaryStatus"] isEqual:@"error"]) self.completionBody.stringValue = @"Summary unavailable. Retry in Sessions.";
    else self.completionBody.stringValue = [self.completion[@"hasSpeech"] boolValue] ? self.message ?: @"Saved to Sessions." : @"Saved to Sessions. No speech was detected.";
    self.completionBody.accessibilityLabel = self.completionBody.stringValue;
    [self updateCopyControls];
}

- (void)renderPanel {
    BOOL offer = [self.phase isEqual:@"offered"], recording = self.recording;
    BOOL completed = [self.phase isEqual:@"finished"] && self.completionSession;
    BOOL reminder = [self.phase isEqual:@"reminder"] && self.calendarOffer;
    BOOL progress = [@[@"starting", @"saving"] containsObject:self.phase];
    BOOL permission = [self.phase isEqual:@"permission"] && self.permissionSource;
    BOOL interrupted = [self.phase isEqual:@"interrupted"] && self.recoveryDraft;
    BOOL hiddenRecording = !self.showIndicator && [@[@"recording", @"starting"] containsObject:self.phase];
    if (self.closed || hiddenRecording || (!offer && !reminder && !recording && !progress && !permission && !interrupted && !completed)) {
        [self.panel orderOut:nil];
        self.panel = nil;
        self.caption = nil;
        return;
    }
    BOOL appearing = !self.panel;
    NSSize size = completed ? NSMakeSize(388, 142) : NSMakeSize(360, 94);
    if (appearing) {
        NSScreen *screen = [self indicatorScreen];
        NSRect frame = JardMeetingCornerFrame(screen.visibleFrame, size, self.panelCorner);
        self.panel = [[JardMeetingPanel alloc] initWithContentRect:frame
            styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel backing:NSBackingStoreBuffered defer:NO];
        self.panel.level = NSStatusWindowLevel;
        self.panel.opaque = NO;
        self.panel.backgroundColor = NSColor.clearColor;
        self.panel.hidesOnDeactivate = NO;
        self.panel.title = @"Jard Meeting";
        __weak typeof(self) weak = self;
        self.panel.placementChanged = ^(NSString *corner, NSScreen *display) { [weak saveCorner:corner screen:display]; };
        self.panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary;
    }
    [self.panel cancelDrag];
    if (!NSEqualSizes(self.panel.frame.size, size)) {
        NSScreen *screen = self.panel.screen ?: [self indicatorScreen];
        [self.panel setFrame:JardMeetingCornerFrame(screen.visibleFrame, size, self.panelCorner) display:YES];
    }
    self.panel.appearance = completed ? [NSAppearance appearanceNamed:NSAppearanceNameDarkAqua] : nil;
    NSVisualEffectView *surface = [[JardMeetingSurface alloc] initWithFrame:NSMakeRect(0, 0, size.width, size.height)];
    surface.material = NSVisualEffectMaterialHUDWindow;
    surface.blendingMode = NSVisualEffectBlendingModeBehindWindow;
    surface.state = NSVisualEffectStateActive;
    surface.wantsLayer = YES;
    surface.layer.cornerRadius = 13;
    surface.layer.masksToBounds = YES;
    self.panel.contentView = surface;
    self.caption = [JardMeetingCaption wrappingLabelWithString:self.message ?: @""];
    self.caption.font = [NSFont systemFontOfSize:13 weight:NSFontWeightSemibold];
    self.caption.frame = NSMakeRect(14, 49, 332, 32);
    [surface addSubview:self.caption];
    if (completed) {
        self.caption.frame = NSMakeRect(14, 106, 327, 23);
        self.caption.maximumNumberOfLines = 1;
        self.caption.lineBreakMode = NSLineBreakByTruncatingTail;
        NSTextField *body = [JardMeetingCaption wrappingLabelWithString:@""];
        body.font = [NSFont systemFontOfSize:12];
        body.frame = NSMakeRect(14, 42, 360, 58);
        body.maximumNumberOfLines = 3;
        body.lineBreakMode = NSLineBreakByTruncatingTail;
        [surface addSubview:body];
        self.completionBody = body;
        NSButton *close = [self button:@"×" action:@selector(dismissCompletion:) area:NSMakeRect(348, 108, 28, 23) into:surface];
        [close setAccessibilityLabel:@"Close meeting capsule"];
        self.incrementalTextButton = [self button:@"Copy New" action:@selector(copyCompletedNewButton:)
            area:NSMakeRect(12, 10, 94, 27) into:surface];
        self.fullTextButton = [self button:@"Copy All" action:@selector(copyCompletedAllButton:)
            area:NSMakeRect(112, 10, 91, 27) into:surface];
        self.agentPromptButton = [self button:@"Copy Agent Prompt" action:@selector(copyMeetingAgentPrompt:)
            area:NSMakeRect(209, 10, 156, 27) into:surface];
        [self updateCompletionContent];
    } else if (permission) {
        [self button:@"Try Again" action:@selector(retryPermissionButton:) area:NSMakeRect(12, 10, 88, 28) into:surface];
        [self button:@"Open System Settings" action:@selector(privacyButton:) area:NSMakeRect(106, 10, 158, 28) into:surface];
        [self button:@"Not Now" action:@selector(dismissPermissionButton:) area:NSMakeRect(268, 10, 80, 28) into:surface];
    } else if (interrupted) {
        [self button:@"Recover Meeting" action:@selector(recoverButton:) area:NSMakeRect(12, 10, 135, 28) into:surface];
        [self button:@"Not Now" action:@selector(dismissRecoveryButton:) area:NSMakeRect(151, 10, 82, 28) into:surface];
    } else if (reminder) {
        NSString *policy = [self policyForCalendar:self.calendarOffer];
        BOOL automatic = self.detection && [policy isEqual:@"auto"], never = [policy isEqual:@"never"];
        if (automatic || never) {
            [self button:automatic ? @"Don’t Record" : @"Not Now" action:@selector(calendarDismiss:)
                area:NSMakeRect(12, 10, 105, 28) into:surface];
            if (self.calendarOffer.link) [self button:@"Join" action:@selector(calendarJoin:)
                area:NSMakeRect(126, 10, 82, 28) into:surface];
        } else {
            [self button:@"Not Now" action:@selector(calendarDismiss:) area:NSMakeRect(12, 10, 83, 28) into:surface];
            [self button:@"Record" action:@selector(calendarRecord:) area:NSMakeRect(101, 10, 78, 28) into:surface];
            if (self.calendarOffer.link) [self button:@"Join & Record" action:@selector(calendarJoinRecord:)
                area:NSMakeRect(186, 10, 146, 28) into:surface];
        }
    } else if (offer) {
        [self button:@"Record" action:@selector(recordButton:) area:NSMakeRect(12, 10, 76, 28) into:surface];
        [self button:@"Not Now" action:@selector(dismissButton:) area:NSMakeRect(93, 10, 85, 28) into:surface];
        NSPopUpButton *policy = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(184, 10, 162, 28) pullsDown:YES];
        [policy addItemWithTitle:@"Provider Options"];
        if (!self.offered.unnamedBrowser) {
            [policy addItemWithTitle:@"Always Record"];
            policy.lastItem.target = self;
            policy.lastItem.action = @selector(alwaysButton:);
        }
        [policy addItemWithTitle:@"Never Offer"];
        policy.lastItem.target = self;
        policy.lastItem.action = @selector(neverButton:);
        [surface addSubview:policy];
    } else if (recording) {
        [self updateCaption];
        if ([self.phase isEqual:@"callEnded"]) {
            [self button:@"Keep Recording" action:@selector(keepButton:) area:NSMakeRect(12, 10, 136, 28) into:surface];
        } else {
            self.incrementalTextButton = [self button:@"Copy New" action:@selector(copyNewButton:)
                area:NSMakeRect(12, 10, 102, 28) into:surface];
            self.fullTextButton = [self button:@"Copy All" action:@selector(copyAllButton:)
                area:NSMakeRect(118, 10, 100, 28) into:surface];
            [self updateCopyControls];
        }
        [self button:@"Stop & Save" action:@selector(stopButton:) area:NSMakeRect(225, 10, 120, 28) into:surface];
    }
    if (appearing) self.panel.alphaValue = 0;
    [self.panel orderFrontRegardless];
    if (appearing) [NSAnimationContext runAnimationGroup:^(NSAnimationContext *animation) {
        animation.duration = self.panel.reduceMotion ? 0.08 : 0.16;
        self.panel.animator.alphaValue = 1;
    }];
}

- (void)shutdown {
    self.closed = YES;
    [self clearCompletion];
    [self.calendar shutdown];
    [self.timer invalidate];
    self.timer = nil;
    [self.panel orderOut:nil];
    self.panel = nil;
}
@end
