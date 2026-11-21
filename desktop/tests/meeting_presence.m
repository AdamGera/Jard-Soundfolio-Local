#import "../jard_meetings.h"
#import "../meeting_presence.h"
#import "../jard_calendar.h"
#import <assert.h>

@interface JardMeetings (Test)
@property(nonatomic) NSTimeInterval lastPresent;
@property(nonatomic, strong) JardMeetingPresence *presence;
@property(nonatomic, strong) JardCalendar *calendar;
@property(nonatomic, strong) NSMutableDictionary *policies;
@property(nonatomic) BOOL autoStop;
- (NSDictionary *)status;
- (void)observed:(NSArray<JardCall *> *)calls;
- (void)observeCalls:(NSArray<JardCall *> *)calls now:(NSTimeInterval)now date:(NSDate *)date;
- (void)pollCalendarAt:(NSDate *)date;
- (NSString *)policyForCall:(JardCall *)call;
- (NSString *)policyForCalendar:(JardCalendarMeeting *)event;
- (void)renderPanel;
- (void)copyNew:(BOOL)onlyNew to:(NSPasteboard *)board reply:(JardDictationReply)reply;
@end

@interface TestMeetings : JardMeetings
@property(nonatomic, copy) NSURL *joined;
@end
@implementation TestMeetings
- (void)renderPanel {}
- (BOOL)openMeetingLink:(NSURL *)url { self.joined = url; return YES; }
@end

@interface TestCalendar : JardCalendar
@property(nonatomic, copy) NSArray<JardCalendarMeeting *> *events;
@property(nonatomic) NSUInteger requests;
@end
@implementation TestCalendar
- (BOOL)connected { return self.requests > 0; }
- (BOOL)reminders { return YES; }
- (NSInteger)lead { return 1; }
- (NSString *)access { return self.connected ? @"granted" : @"notDetermined"; }
- (NSArray<JardCalendarMeeting *> *)meetings { return self.connected ? self.events ?: @[] : @[]; }
- (void)connect:(void (^)(NSString *))reply { self.requests++; reply(nil); }
@end

static void exerciseCalendar(void) {
    NSDate *now = NSDate.date;
    JardCalendarMeeting *event = JardCalendarOccurrence(@"repeat", @"Design sync", @"work", [now dateByAddingTimeInterval:100],
        [now dateByAddingTimeInterval:1700], NO, @[@"Sam", @"Sam"],
        @[@"https://meet.google.com.example.com/abc-defg-hij", @"Join <https://meet.google.com/abc-defg-hij>."]);
    assert([event.provider isEqual:@"Google Meet"] && [event.link.host isEqual:@"meet.google.com"] && event.attendees.count == 1);
    assert(!JardCalendarOccurrence(@"hidden", @"Ignore", @"work", event.start, event.end, YES, @[@"Sam"], @[]));
    assert(!JardCalendarOccurrence(@"fake", @"Ignore", @"work", event.start, event.end, NO, @[],
        @[@"https://meet.google.com.evil.example/abc-defg-hij", @"https://meet.google.com@evil.example/abc-defg-hij",
          @"https://zoom.us/download", @"https://zoom.us:8888/j/123", @"https://teams.microsoft.com/home"]));
    assert(!JardCalendarMatchesCall(event, JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com/xyz-wxyz-xyz"], @"com.google.chrome")));

    __block NSDictionary *take = nil;
    TestMeetings *meeting = [[TestMeetings alloc] initWithManaged:NO start:^(NSDictionary *options, BOOL prompt, JardDictationReply reply) {
        assert(prompt); take = options; reply(@{}, nil);
    } send:^(NSString *command, NSDictionary *options, JardDictationReply reply) { assert(NO); } changed:nil];
    [meeting.policies removeAllObjects];
    TestCalendar *calendar = [[TestCalendar alloc] initWithChanged:nil];
    calendar.events = @[event];
    meeting.calendar = calendar;
    [meeting request:@{@"action": @"status"} reply:^(id result, NSString *error) {
        assert(!error && ![result[@"calendar"][@"connected"] boolValue] && calendar.requests == 0);
    }];
    [meeting request:@{@"action": @"calendarConnect"} reply:^(id result, NSString *error) {
        assert(!error && [result[@"calendar"][@"connected"] boolValue] && calendar.requests == 1);
    }];
    [meeting pollCalendarAt:now];
    assert([[meeting status][@"phase"] isEqual:@"idle"]);
    [meeting pollCalendarAt:[now dateByAddingTimeInterval:41]];
    assert([[meeting status][@"phase"] isEqual:@"reminder"]);
    [meeting request:@{@"action": @"calendarEvent", @"id": event.identifier, @"choice": @"dismiss"}
        reply:^(id result, NSString *error) { assert(!error && !meeting.joined); }];
    JardCall *browser = JardMeetingBrowserCall(event.link, @"com.google.chrome");
    [meeting observeCalls:@[browser] now:10 date:now];
    [meeting observeCalls:@[browser] now:12 date:now];
    [meeting pollCalendarAt:[now dateByAddingTimeInterval:50]];
    assert([[meeting status][@"phase"] isEqual:@"idle"]);

    JardCalendarMeeting *moved = JardCalendarOccurrence(@"repeat", event.title, @"work", [now dateByAddingTimeInterval:200],
        event.end, NO, event.attendees, @[event.link.absoluteString]);
    assert(![event.identifier isEqual:moved.identifier]);
    calendar.events = @[moved];
    [meeting pollCalendarAt:[now dateByAddingTimeInterval:145]];
    assert([[meeting status][@"phase"] isEqual:@"reminder"]);
    [meeting request:@{@"action": @"calendarEvent", @"id": moved.identifier, @"choice": @"join"}
        reply:^(id result, NSString *error) { assert(!error && [meeting.joined isEqual:moved.link] && !take); }];
    [meeting request:@{@"action": @"calendarEvent", @"id": moved.identifier, @"choice": @"joinRecord"}
        reply:^(id result, NSString *error) {
            assert(!error && [take[@"meetingCalendar"][@"title"] isEqual:moved.title]);
            assert([take[@"meetingCalendar"][@"attendees"] isEqual:@[@"Sam"]]);
        }];
    [meeting shutdown];
}

static void exerciseAutoStop(void) {
    NSString *chrome = @"com.google.chrome";
    JardCall *original = JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com/abc-defg-hij"], chrome);
    JardCall *changed = JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com/abc-defg-xyz"], chrome);
    JardCall *third = JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com/xyz-wxyz-xyz"], chrome);
    __block NSString *identifier;
    __block NSUInteger stops = 0;
    TestMeetings *meeting = [[TestMeetings alloc] initWithManaged:NO start:^(NSDictionary *options, BOOL prompt, JardDictationReply reply) {
        assert(!prompt); identifier = options[@"meeting"]; reply(@{}, nil);
    } send:^(NSString *command, NSDictionary *options, JardDictationReply reply) {
        assert([command isEqual:@"jard.stopMeeting"] && [options[@"meeting"] isEqual:identifier]);
        stops++; reply(nil, nil);
    } changed:nil];
    [meeting.policies removeAllObjects];
    meeting.policies[original.provider] = @{@"label": original.label, @"policy": @"auto"};
    meeting.autoStop = YES;
    NSDate *date = NSDate.date;
    NSTimeInterval now = NSProcessInfo.processInfo.systemUptime;
    [meeting observeCalls:@[original] now:now date:date];
    [meeting observeCalls:@[original] now:now + 2 date:date];
    assert(identifier);
    [meeting handleCapture:@{@"meeting": identifier, @"event": @"started"}];
    original.inputActive = NO;
    [meeting observeCalls:@[original] now:now + 6 date:date];
    assert([[meeting status][@"phase"] isEqual:@"recording"]);
    [meeting observeCalls:@[] now:now + 9 date:date];
    assert([[meeting status][@"phase"] isEqual:@"callEnded"]);
    [meeting observeCalls:@[original] now:now + 10 date:date];
    assert([[meeting status][@"phase"] isEqual:@"recording"]);
    [meeting observeCalls:@[changed] now:now + 11 date:date];
    [meeting observeCalls:@[changed] now:now + 14 date:date];
    assert([[meeting status][@"phase"] isEqual:@"recording"]);
    [meeting observeCalls:@[changed] now:now + 15 date:date];
    assert([[meeting status][@"phase"] isEqual:@"callEnded"]);
    [meeting request:@{@"action": @"keepRecording"} reply:^(id result, NSString *error) { assert(!error); }];
    [meeting observeCalls:@[changed] now:now + 48 date:date];
    assert(stops == 0 && [[meeting status][@"phase"] isEqual:@"recording"]);
    [meeting observeCalls:@[third] now:now + 49 date:date];
    [meeting observeCalls:@[third] now:now + 53 date:date];
    assert([[meeting status][@"phase"] isEqual:@"callEnded"]);
    [meeting observeCalls:@[third] now:now + 82 date:date];
    assert(stops == 0);
    [meeting observeCalls:@[third] now:now + 83 date:date];
    assert(stops == 1 && [[meeting status][@"phase"] isEqual:@"saving"]);
    [meeting shutdown];
}

static void exerciseProviderSettings(void) {
    NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
    NSString *key = @"JardMeetingProviderPolicies";
    id previous = [defaults objectForKey:key];
    [defaults setObject:@{
        @"site:zoom.us": @{@"provider": @"site:zoom.us", @"label": @"Zoom", @"policy": @"never"},
        @"browser:com.google.chrome": @{@"provider": @"browser:com.google.chrome", @"label": @"Google Chrome", @"policy": @"never"},
        @"app:webex": @[@"invalid"],
    } forKey:key];
    __block NSUInteger automaticStarts = 0;
    TestMeetings *meeting = [[TestMeetings alloc] initWithManaged:NO
        start:^(NSDictionary *options, BOOL prompt, JardDictationReply reply) {
            assert(!prompt && [options[@"meetingLabel"] isEqual:@"Zoom"]);
            automaticStarts++;
            reply(@{}, nil);
        } send:nil changed:nil];
    NSArray *rows = [meeting status][@"providerPolicies"];
    assert(rows.count == 20);
    assert([rows[1][@"provider"] isEqual:@"app:zoom"] && [rows[1][@"policy"] isEqual:@"never"]);
    assert([rows[2][@"provider"] isEqual:@"site:meet.google.com"] && [rows[2][@"policy"] isEqual:@"ask"]);
    NSString *chrome = @"com.google.chrome";
    JardCall *zoom = JardMeetingDirectCall(@"us.zoom.xos");
    JardCall *webZoom = JardMeetingBrowserCall([NSURL URLWithString:@"https://zoom.us/j/123"], chrome);
    JardCall *zoomAlias = JardMeetingBrowserCall([NSURL URLWithString:@"https://zoomgov.com/j/123"], @"com.apple.safari");
    JardCall *unnamed = JardMeetingUnnamedBrowser(chrome, @"Google Chrome");
    NSDate *date = NSDate.date;
    JardCalendarMeeting *invite = JardCalendarOccurrence(@"zoom", @"Design sync", @"work", date,
        [date dateByAddingTimeInterval:1800], NO, @[], @[@"https://zoom.us/j/123"]);
    assert(invite.link);
    void (^save)(NSString *, NSString *) = ^(NSString *provider, NSString *policy) {
        [meeting request:@{@"action": @"setPolicy", @"provider": provider, @"policy": policy}
            reply:^(id result, NSString *error) { assert(result && !error); }];
    };
    save(zoom.provider, @"auto");
    assert(automaticStarts == 0 && [[meeting policyForCall:zoomAlias] isEqual:@"auto"]);
    assert([[meeting policyForCall:webZoom] isEqual:@"never"]);
    assert([[meeting policyForCalendar:invite] isEqual:@"never"]);
    save(webZoom.provider, @"ask");
    assert([[meeting policyForCall:webZoom] isEqual:@"ask"] && [[meeting policyForCall:unnamed] isEqual:@"never"]);
    assert([[meeting policyForCalendar:invite] isEqual:@"ask"]);
    for (NSDictionary *change in @[
        @{@"provider": unnamed.provider, @"policy": @"auto"},
        @{@"provider": @"site:unrecognized", @"policy": @"never"},
        @{@"provider": zoom.provider, @"policy": @"unexpected"},
    ]) {
        NSMutableDictionary *request = [change mutableCopy];
        request[@"action"] = @"setPolicy";
        [meeting request:request reply:^(id result, NSString *error) { assert(!result && error); }];
    }
    [meeting observeCalls:@[webZoom] now:30 date:date];
    [meeting observeCalls:@[webZoom] now:32 date:date];
    assert(automaticStarts == 0 && [[[meeting status] objectForKey:@"phase"] isEqual:@"offered"]);
    save(webZoom.provider, @"never");
    assert([[[meeting status] objectForKey:@"phase"] isEqual:@"idle"]);
    [meeting observeCalls:@[zoom] now:35 date:date];
    [meeting observeCalls:@[zoom] now:37 date:date];
    assert(automaticStarts == 1);
    [meeting shutdown];
    TestMeetings *reopened = [[TestMeetings alloc] initWithManaged:NO start:nil send:nil changed:nil];
    assert([[reopened policyForCall:zoom] isEqual:@"auto"] && [[reopened policyForCall:webZoom] isEqual:@"never"]);
    [reopened shutdown];
    TestMeetings *managed = [[TestMeetings alloc] initWithManaged:YES start:nil send:nil changed:nil];
    [managed request:@{@"action": @"setPolicy", @"provider": zoom.provider, @"policy": @"ask"}
        reply:^(id result, NSString *error) { assert(!result && [error containsString:@"managed"]); }];
    [managed shutdown];
    assert([[[defaults dictionaryForKey:key][zoom.provider] objectForKey:@"policy"] isEqual:@"auto"]);
    [defaults setObject:previous forKey:key];
    puts("Provider settings listed built-in and scoped options, retained legacy choices, and applied only authorized recording policies.");
}

int main(void) {
    @autoreleasepool {
        exerciseCalendar();
        exerciseAutoStop();
        exerciseProviderSettings();
        JardCall *zoom = JardMeetingDirectCall(@"us.zoom.CptHost");
        assert([zoom.identity isEqual:JardMeetingDirectCall(@"us.zoom.xos.helper").identity]);
        assert(!JardMeetingDirectCall(@"org.mozilla.firefox"));
        JardCall *signal = JardMeetingDirectCall(@"org.whispersystems.signal-desktop");
        assert(signal.slow);
        NSString *chrome = @"com.google.chrome";
        JardCall *meet = JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com/meet/abc-defg-hij"], chrome);
        JardCall *other = JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com/abc-defg-xyz"], chrome);
        assert([meet.label isEqual:@"Google Meet"] && ![meet.identity isEqual:other.identity]);
        assert(!JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com/lookup/name"], chrome));
        assert(!JardMeetingBrowserCall([NSURL URLWithString:@"https://meet.google.com.example.com/abc-defg-hij"], chrome));
        assert(!JardMeetingBrowserCall([NSURL URLWithString:@"http://meet.google.com/abc-defg-hij"], chrome));
        JardCall *teams = JardMeetingBrowserCall([NSURL URLWithString:@"https://teams.cloud.microsoft/meet/123"], chrome);
        assert([teams.provider isEqual:JardMeetingDirectCall(@"com.microsoft.teams2").provider]);
        JardCall *unnamed = JardMeetingUnnamedBrowser(chrome, @"Chrome");
        assert(unnamed.unnamedBrowser && unnamed.slow);

        JardMeetingPresence *watch = JardMeetingPresence.new;
        assert(![watch offerFromCalls:@[zoom] now:10]);
        assert(![watch offerFromCalls:@[zoom] now:11]);
        assert([watch offerFromCalls:@[zoom] now:12] == zoom);
        [watch snooze:zoom absence:60 now:12];
        assert(![watch offerFromCalls:@[] now:35]);
        assert(![watch offerFromCalls:@[zoom] now:36]);
        assert(![watch offerFromCalls:@[zoom] now:37]);
        assert(![watch offerFromCalls:@[] now:96]);
        assert(![watch offerFromCalls:@[zoom] now:98]);
        assert([watch offerFromCalls:@[zoom] now:100] == zoom);
        [watch resetObservations];
        for (NSUInteger now = 120; now < 140; now++) assert(![watch offerFromCalls:@[unnamed] now:now]);
        assert([watch offerFromCalls:@[unnamed] now:140] == unnamed);
        assert(![watch offerFromCalls:@[unnamed] now:151]);

        __block NSDictionary *take;
        __block BOOL refuse = YES;
        __block NSUInteger cutoff = 96000;
        __block BOOL complete = YES;
        __block BOOL settled = NO;
        NSString *saved = NSUUID.UUID.UUIDString.lowercaseString;
        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        NSString *previous = [defaults stringForKey:@"JardLastMeetingSession"];
        NSArray *finalTurns = @[
            @{@"speaker": @2, @"start": @0.5, @"end": @1.7, @"text": @"First phrase rewritten."},
            @{@"speaker": @1, @"start": @0.9, @"end": @1.6, @"text": @"Overlapping local note."},
            @{@"speaker": @2, @"start": @2.8, @"end": @3.2, @"text": @"A recovered gap."},
            @{@"speaker": @2, @"start": @4.0, @"end": @4.9, @"text": @"Second phrase rewritten."},
            @{@"speaker": @2, @"start": @6.0, @"end": @7.0, @"text": @"Tail."}
        ];
        TestMeetings *meeting = [[TestMeetings alloc] initWithManaged:NO start:^(NSDictionary *options, BOOL prompt, JardDictationReply reply) {
            assert(prompt);
            assert([options[@"mode"] isEqual:@"both"] && [options[@"live"] boolValue]);
            take = options;
            reply(@{}, refuse ? @"Microphone denied" : nil);
        } send:^(NSString *command, NSDictionary *options, JardDictationReply reply) {
            if ([command isEqual:@"jard.meetingCompletion"]) {
                assert([options[@"session"] isEqual:saved]);
                reply(@{@"status": @"complete", @"hasSpeech": @YES}, nil);
                return;
            }
            if ([command isEqual:@"jard.meetingExcerpt"]) {
                assert([options[@"session"] isEqual:saved]);
                reply(@{@"turns": settled ? finalTurns : @[finalTurns[0]], @"settled": @(settled)}, nil);
                return;
            }
            assert([options[@"meeting"] isEqual:take[@"meeting"]]);
            if ([command isEqual:@"jard.copyMeeting"]) reply(@{@"through": @(cutoff), @"complete": @(complete)}, nil);
            else { assert([command isEqual:@"jard.stopMeeting"]); reply(nil, nil); }
        } changed:nil];
        NSDictionary *start = @{@"action": @"start", @"languages": @[@"en"]};
        [meeting request:start reply:^(id result, NSString *error) { assert(error); }];
        assert(!meeting.busy && [[meeting status][@"phase"] isEqual:@"error"]);
        refuse = NO;
        [meeting suspendOffers:YES];
        [meeting request:start reply:^(id result, NSString *error) { assert(error); }];
        [meeting suspendOffers:NO];

        // Use a recognized call that the real watcher already held for two seconds.
        NSTimeInterval now = NSProcessInfo.processInfo.systemUptime;
        [meeting.presence offerFromCalls:@[zoom] now:now - 2];
        [meeting observed:@[zoom]];
        assert([[meeting status][@"phase"] isEqual:@"offered"]);
        [meeting request:start reply:^(id result, NSString *error) { assert(!error && [result[@"busy"] boolValue]); }];
        NSString *identifier = take[@"meeting"];
        assert([identifier isEqual:identifier.lowercaseString]);
        [meeting handleCapture:@{@"meeting": @"stale", @"event": @"started"}];
        assert(!meeting.recording);
        [meeting handleCapture:@{@"meeting": identifier, @"event": @"started", @"session": saved}];
        assert(meeting.recording);
        [meeting request:start reply:^(id result, NSString *error) { assert(error); }];
        assert(meeting.canPaste);
        [meeting handleCapture:@{@"meeting": identifier, @"event": @"preview", @"through": @96000,
            @"deltaSegments": @[@{@"speaker": @2, @"start": @0.4, @"end": @1.8, @"text": @"First phrase."}]}];
        [meeting handleCapture:@{@"meeting": identifier, @"event": @"preview", @"through": @240000,
            @"deltaSegments": @[@{@"speaker": @2, @"start": @4.1, @"end": @4.8, @"text": @"Second phrase."}]}];
        NSPasteboard *pasteboard = NSPasteboard.pasteboardWithUniqueName;
        [meeting copyNew:YES to:pasteboard reply:^(id result, NSString *error) { assert(result && !error); }];
        assert([[pasteboard stringForType:NSPasteboardTypeString] isEqual:@"Them: First phrase."]);
        [meeting copyNew:YES to:pasteboard reply:^(id result, NSString *error) { assert(!result && error); }];
        cutoff = 240000;
        [meeting preparePaste:^(NSString *text, void (^didPaste)(BOOL)) {
            assert([text isEqual:@"Them: Second phrase."]);
            assert([[meeting status][@"copying"] boolValue]);
            didPaste(NO);
        }];
        [meeting preparePaste:^(NSString *text, void (^didPaste)(BOOL)) {
            assert([text isEqual:@"Them: Second phrase."]);
            didPaste(YES);
        }];
        [meeting copyNew:YES to:pasteboard reply:^(id result, NSString *error) { assert(!result && error); }];
        complete = NO;
        [meeting copyNew:NO to:pasteboard reply:^(id result, NSString *error) { assert([result[@"copyPartial"] boolValue] && !error); }];
        assert([[pasteboard stringForType:NSPasteboardTypeString] isEqual:@"Them: First phrase. Second phrase."]);
        meeting.lastPresent = now - 4;
        [meeting observed:@[]];
        assert([[meeting status][@"phase"] isEqual:@"callEnded"]);
        [meeting observed:@[zoom]];
        assert([[meeting status][@"phase"] isEqual:@"recording"]);
        [meeting request:@{@"action": @"stop"} reply:^(id result, NSString *error) { assert(!error); }];
        assert([[meeting status][@"phase"] isEqual:@"saving"]);
        [meeting handleCapture:@{@"meeting": identifier, @"event": @"accepted", @"id": saved}];
        assert(!meeting.busy && [[meeting status][@"session"] isEqual:saved] && meeting.canPaste);
        [meeting copyNew:YES to:pasteboard reply:^(id result, NSString *error) { assert(!result && error); }];
        // A draft with nothing new must leave the shortcut ready for final publication.
        assert(meeting.canPaste);
        settled = YES;
        NSString *expected = @"You: Overlapping local note.\n\nThem: A recovered gap. Tail.";
        [meeting preparePaste:^(NSString *text, void (^didPaste)(BOOL)) {
            assert([text isEqual:expected]);
            didPaste(NO);
        }];
        [meeting copyNew:YES to:pasteboard reply:^(id result, NSString *error) { assert(result && !error); }];
        assert([[pasteboard stringForType:NSPasteboardTypeString] isEqual:expected]);
        assert(!meeting.canPaste);
        [meeting copyNew:YES to:pasteboard reply:^(id result, NSString *error) { assert(!result && error); }];
        [meeting copyNew:NO to:pasteboard reply:^(id result, NSString *error) { assert(result && !error); }];
        assert([[pasteboard stringForType:NSPasteboardTypeString] containsString:@"First phrase rewritten."]);
        [pasteboard releaseGlobally];
        [meeting shutdown];

        NSString *recovered = NSUUID.UUID.UUIDString.lowercaseString;
        TestMeetings *reopened = [[TestMeetings alloc] initWithManaged:NO start:nil
            send:^(NSString *command, NSDictionary *options, JardDictationReply reply) {
                if ([command isEqual:@"jard.meetingDrafts"]) reply(@[], nil);
                else if ([command isEqual:@"jard.latestMeeting"]) reply(@{@"session": recovered, @"recovering": @NO}, nil);
                else {
                    assert([command isEqual:@"jard.meetingExcerpt"] && [options[@"session"] isEqual:recovered]);
                    reply(@{@"turns": @[finalTurns[0]], @"settled": @YES}, nil);
                }
            } changed:nil];
        [reopened workspaceReady];
        assert(reopened.canPaste);
        [reopened preparePaste:^(NSString *text, void (^didPaste)(BOOL)) {
            assert([text isEqual:@"Them: First phrase rewritten."]);
            didPaste(YES);
        }];
        assert(!reopened.canPaste);
        [reopened shutdown];
        if (previous) [defaults setObject:previous forKey:@"JardLastMeetingSession"];
        else [defaults removeObjectForKey:@"JardLastMeetingSession"];
        puts("Calendar, automatic call end, and success-only per-source meeting copy through finalization passed.");
    }
}
