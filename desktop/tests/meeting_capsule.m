#import "../jard_meeting_panel.h"
#import "../jard_meetings.h"
#import <assert.h>

@interface JardMeetings (Fixture)
@property(nonatomic, readonly) JardMeetingPanel *panel;
@property(nonatomic, readonly) NSTextField *caption;
@property(nonatomic, readonly) NSTextField *completionBody;
@property(nonatomic, readonly) NSTimeInterval completionDeadline;
- (NSDictionary *)status;
- (void)finish:(NSString *)error session:(NSString *)session;
- (void)updateCompletionAt:(NSTimeInterval)now pointer:(NSPoint)pointer;
- (void)copyCompletedMeeting:(BOOL)onlyNew to:(NSPasteboard *)board;
- (void)copyMeetingAgentPromptTo:(NSPasteboard *)board;
@end

@interface MotionlessPanel : JardMeetingPanel
@end
@implementation MotionlessPanel
- (BOOL)reduceMotion { return YES; }
@end

static NSButton *button(JardMeetingPanel *panel, NSString *label) {
    for (NSView *view in panel.contentView.subviews) {
        if ([view isKindOfClass:NSButton.class] && [((NSButton *)view).title isEqual:label]) return (NSButton *)view;
    }
    return nil;
}

static void testPlacement(NSScreen *screen) {
    NSRect visible = screen.visibleFrame;
    NSSize size = NSMakeSize(360, 94);
    for (NSString *corner in @[@"topLeft", @"topRight", @"bottomLeft", @"bottomRight"]) {
        NSRect frame = JardMeetingCornerFrame(visible, size, corner);
        assert(NSContainsRect(visible, frame));
        assert([JardNearestMeetingCorner(visible, NSMakePoint(NSMidX(frame), NSMidY(frame))) isEqual:corner]);
    }
    MotionlessPanel *panel = [[MotionlessPanel alloc] initWithContentRect:JardMeetingCornerFrame(visible, size, @"topRight")
        styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel backing:NSBackingStoreBuffered defer:NO];
    __block NSString *saved = nil;
    panel.placementChanged = ^(NSString *corner, NSScreen *display) {
        assert(display == screen); saved = corner;
    };
    NSRect original = panel.frame;
    NSPoint start = NSMakePoint(NSMidX(original), NSMidY(original));
    NSRect destination = JardMeetingCornerFrame(visible, size, @"bottomLeft");
    NSPoint end = NSMakePoint(NSMidX(destination), NSMidY(destination));

    [panel beginDragAt:start time:1];
    [panel dragTo:end time:1.2];
    assert(NSEqualRects(panel.frame, destination));
    [panel cancelDrag];
    assert(NSEqualRects(panel.frame, original) && saved == nil);

    [panel beginDragAt:start time:2];
    [panel dragTo:end time:2.2];
    [panel endDragAt:end time:2.21];
    assert([saved isEqual:@"bottomLeft"] && NSEqualRects(panel.frame, destination));
    assert(!panel.canBecomeKeyWindow && !panel.canBecomeMainWindow);
    [panel orderOut:nil];
}

static void testMeetingWindow(void) {
    NSString *draft = NSUUID.UUID.UUIDString.lowercaseString;
    __block NSString *take = nil;
    __block NSInteger attempts = 0, stops = 0, recoveries = 0;
    __block JardDictationReply readCompletion = nil;
    __block JardDictationReply readMarkdown = nil;
    JardMeetings *meeting = [[JardMeetings alloc] initWithManaged:NO start:^(NSDictionary *options, BOOL prompt, JardDictationReply reply) {
        assert(prompt);
        take = options[@"meeting"];
        attempts++;
        if (attempts < 3) reply(@{@"permission": attempts == 1 ? @"mic" : @"system"}, @"Fixture permission denial.");
        else reply(@{}, nil);
    } send:^(NSString *command, NSDictionary *options, JardDictationReply reply) {
        if ([command isEqual:@"jard.stopMeeting"]) {
            assert([options[@"meeting"] isEqual:take]);
            stops++;
            reply(nil, nil);
        } else if ([command isEqual:@"jard.meetingCompletion"]) {
            assert([options[@"session"] isEqual:draft] && !readCompletion);
            readCompletion = [reply copy];
        } else if ([command isEqual:@"jard.meetingExcerpt"]) {
            assert([options[@"session"] isEqual:draft]);
            reply(@{@"settled": @YES, @"turns": @[@{@"speaker": @1, @"start": @2, @"end": @4, @"text": @"Approve the plan."}]}, nil);
        } else if ([command isEqual:@"jard.meetingMarkdown"]) {
            assert([options[@"session"] isEqual:draft] && !readMarkdown);
            readMarkdown = [reply copy];
        } else {
            assert([command isEqual:@"jard.recoverMeeting"] && [options[@"id"] isEqual:draft]);
            recoveries++;
            reply(@{@"id": draft}, nil);
        }
    } changed:nil];
    __block NSUInteger audioSettingsOpened = 0;
    meeting.showSystemAudioSettings = ^{ audioSettingsOpened++; };
    void (^accept)(id, NSString *) = ^(id result, NSString *error) { assert(!error); };
    [meeting request:@{@"action": @"start"} reply:^(id result, NSString *error) { assert(error); }];
    assert([[meeting status][@"permission"] isEqual:@"mic"] && meeting.panel.visible);
    assert([meeting.caption.stringValue containsString:@"Microphone"]);
    assert([button(meeting.panel, @"Try Again").accessibilityLabel isEqual:@"Try Again"]);
    [button(meeting.panel, @"Try Again") performClick:nil];
    assert([[meeting status][@"permission"] isEqual:@"system"] && [meeting.caption.stringValue containsString:@"Mac audio"]);
    [button(meeting.panel, @"Open System Settings") performClick:nil];
    [meeting request:@{@"action": @"recordingPrivacy"} reply:accept];
    assert(audioSettingsOpened == 2);
    [button(meeting.panel, @"Try Again") performClick:nil];
    [meeting handleCapture:@{@"meeting": take, @"event": @"started", @"microphone": @"Built-in"}];
    assert([[meeting status][@"microphone"] isEqual:@"Built-in"]);
    [meeting handleCapture:@{@"meeting": take, @"event": @"sourceChanged", @"microphone": @"Headset"}];
    assert([[meeting status][@"microphone"] isEqual:@"Headset"] && meeting.recording);
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingSystemAudio", @"available": @NO}];
    assert([[meeting status][@"systemAudioUnavailable"] boolValue] && meeting.recording);
    assert([meeting.caption.accessibilityLabel containsString:@"Mac audio reconnecting"]);
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingSystemAudio", @"available": @YES}];
    assert(![[meeting status][@"systemAudioUnavailable"] boolValue] && meeting.recording);
    [meeting handleCapture:@{@"meeting": take, @"event": @"levels", @"seconds": @8}];
    assert([meeting.caption.accessibilityLabel containsString:@"0:08"]);
    [meeting handleCapture:@{@"meeting": NSUUID.UUID.UUIDString, @"event": @"meetingTitle", @"title": @"Other take"}];
    assert([[meeting status][@"title"] isEqual:@""]);
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingTitle", @"title": @"Review the launch"}];
    assert([[meeting status][@"title"] isEqual:@"Review the launch"]);
    assert([meeting.caption.accessibilityLabel containsString:@"Review the launch · 0:08"]);
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingMicWarning", @"active": @YES}];
    assert([[meeting status][@"microphoneSilent"] boolValue] && meeting.recording);
    assert([meeting.caption.accessibilityLabel containsString:@"Microphone silent"]);
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingMicWarning", @"active": @NO}];
    assert(![[meeting status][@"microphoneSilent"] boolValue]);
    assert([meeting.caption.accessibilityLabel containsString:@"Review the launch"]);
    NSString *longTitle = @"Extraordinary international photographic calibration workshop preparations";
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingTitle", @"title": longTitle}];
    assert([meeting.caption.stringValue hasPrefix:@"0:08 · "]);
    assert([meeting.caption.accessibilityLabel containsString:longTitle]);
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingTitle", @"title": @"Review the launch"}];
    assert([button(meeting.panel, @"Copy New").accessibilityLabel isEqual:@"Copy new meeting transcript"]);
    assert([button(meeting.panel, @"Stop & Save").accessibilityLabel isEqual:@"Stop & Save"]);

    [meeting request:@{@"action": @"indicator", @"enabled": @NO} reply:accept];
    assert(meeting.recording && !meeting.panel);
    [meeting handleCapture:@{@"meeting": take, @"event": @"levels", @"seconds": @9}];
    [meeting request:@{@"action": @"indicatorCorner", @"corner": @"topLeft"} reply:accept];
    [meeting request:@{@"action": @"indicator", @"enabled": @YES} reply:accept];
    assert(meeting.panel.visible && [meeting.caption.accessibilityLabel containsString:@"0:09"]);
    assert(NSMinX(meeting.panel.frame) == NSMinX(meeting.panel.screen.visibleFrame) + 18);
    [meeting request:@{@"action": @"indicator", @"enabled": @NO} reply:accept];
    [meeting request:@{@"action": @"stop"} reply:accept];
    assert(stops == 1);
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingTitle", @"title": @"Late title"}];
    assert([[meeting status][@"title"] isEqual:@"Review the launch"]);

    [meeting handleCapture:@{@"meeting": take, @"event": @"error", @"draft": draft}];
    assert([[meeting status][@"phase"] isEqual:@"interrupted"] && meeting.panel.visible);
    [button(meeting.panel, @"Recover Meeting") performClick:nil];
    assert(recoveries == 1 && [[meeting status][@"session"] isEqual:draft] && !meeting.busy);
    assert(meeting.panel.visible && meeting.keepsRunning && readCompletion);
    assert([meeting.completionBody.stringValue isEqual:@"Transcribing the saved meeting…"]);
    assert(!button(meeting.panel, @"Copy New").enabled && !button(meeting.panel, @"Copy Agent Prompt").enabled);
    NSDictionary *ready = @{@"status": @"complete", @"duration": @125, @"title": @"Approved plan",
                            @"summaryStatus": @"queued", @"hasSpeech": @YES};
    JardDictationReply reply = readCompletion;
    readCompletion = nil;
    reply(ready, nil);
    assert([meeting.caption.stringValue isEqual:@"2:05 · Approved plan"]);
    assert([meeting.completionBody.stringValue containsString:@"local Qwen"]);
    assert([meeting.panel.appearance.name isEqual:NSAppearanceNameDarkAqua]);
    assert(button(meeting.panel, @"Copy New").enabled && button(meeting.panel, @"Copy Agent Prompt").enabled);
    NSPasteboard *board = NSPasteboard.pasteboardWithUniqueName;
    [meeting copyCompletedMeeting:YES to:board];
    assert([[board stringForType:NSPasteboardTypeString] containsString:@"Approve the plan."]);
    [meeting copyCompletedMeeting:NO to:board];
    assert(readMarkdown);
    reply = readMarkdown;
    readMarkdown = nil;
    reply(@{@"text": @"# Approved plan\n\nApprove the plan."}, nil);
    assert([[board stringForType:NSPasteboardTypeString] hasPrefix:@"# Approved plan"]);
    [meeting copyCompletedMeeting:NO to:board];
    reply = readMarkdown;
    readMarkdown = nil;
    [board clearContents];
    [board setString:@"A newer copy" forType:NSPasteboardTypeString];
    reply(@{@"text": @"# A late Markdown result"}, nil);
    assert([[board stringForType:NSPasteboardTypeString] isEqual:@"A newer copy"]);
    [meeting copyCompletedMeeting:NO to:board];
    reply = readMarkdown;
    readMarkdown = nil;
    reply(nil, @"Markdown unavailable");
    assert([[board stringForType:NSPasteboardTypeString] containsString:@"Approve the plan."]);
    [meeting copyMeetingAgentPromptTo:board];
    NSString *command = [NSString stringWithFormat:@"jardctl meetings show %@ --md", draft];
    assert([[board stringForType:NSPasteboardTypeString] containsString:command]);
    [board releaseGlobally];

    NSTimeInterval deadline = meeting.completionDeadline;
    assert(deadline > NSProcessInfo.processInfo.systemUptime + 13);
    NSPoint inside = NSMakePoint(NSMidX(meeting.panel.frame), NSMidY(meeting.panel.frame));
    [meeting updateCompletionAt:deadline + 1 pointer:inside];
    assert(meeting.panel.visible && readCompletion);
    reply = readCompletion;
    readCompletion = nil;
    NSMutableDictionary *lateSummary = ready.mutableCopy;
    lateSummary[@"summary"] = @"The group approved the plan.";
    lateSummary[@"summaryStatus"] = @"ready";
    [button(meeting.panel, @"×") performClick:nil];
    reply(lateSummary, nil);
    assert(!meeting.panel);

    [meeting finish:nil session:draft];
    reply = readCompletion;
    readCompletion = nil;
    reply(lateSummary, nil);
    assert([meeting.completionBody.stringValue isEqual:@"The group approved the plan."]);
    [meeting updateCompletionAt:meeting.completionDeadline + 1 pointer:NSMakePoint(-100000, -100000)];
    assert(!meeting.panel);
    [meeting shutdown];
}

static void testTitledMeeting(void) {
    NSString *title = @"A named meeting";
    NSString *session = NSUUID.UUID.UUIDString.lowercaseString;
    __block NSUInteger attempts = 0;
    __block NSString *take;
    JardMeetings *meeting = [[JardMeetings alloc] initWithManaged:NO start:^(NSDictionary *options, BOOL prompt, JardDictationReply reply) {
        assert([options[@"meetingTitle"] isEqual:title]);
        take = options[@"meeting"];
        assert(prompt == (attempts > 0));
        if (!attempts++) reply(@{@"permission": @"mic"}, @"Fixture permission denial.");
        else reply(@{}, nil);
    } send:^(NSString *command, NSDictionary *options, JardDictationReply reply) {
        assert([command isEqual:@"jard.meetingCompletion"] && [options[@"session"] isEqual:session]);
        reply(@{@"status": @"complete", @"duration": @6, @"title": title, @"hasSpeech": @YES}, nil);
    } changed:nil];
    [meeting request:@{@"action": @"indicator", @"enabled": @YES} reply:^(id result, NSString *error) { assert(!error); }];
    [meeting startWithEstablishedPermissionsTitle:title reply:^(id result, NSString *error) { assert(error && !result); }];
    assert([[meeting status][@"permission"] isEqual:@"mic"] && [[meeting status][@"title"] isEqual:title]);
    [button(meeting.panel, @"Try Again") performClick:nil];
    assert(attempts == 2);
    [meeting handleCapture:@{@"meeting": take, @"event": @"started", @"session": session}];
    [meeting startWithEstablishedPermissionsTitle:@"Another name" reply:^(id result, NSString *error) { assert(error && !result); }];
    [meeting handleCapture:@{@"meeting": take, @"event": @"meetingTitle", @"title": @"Late generated title"}];
    assert(attempts == 2 && [[meeting status][@"title"] isEqual:title]);
    assert([meeting.caption.stringValue containsString:title]);
    [meeting handleCapture:@{@"meeting": take, @"event": @"accepted", @"id": session}];
    assert([[meeting status][@"title"] isEqual:title] && [meeting.caption.stringValue isEqual:@"0:06 · A named meeting"]);
    [meeting shutdown];
}

static void testManagedMeetings(void) {
    NSUserDefaults *preferences = NSUserDefaults.standardUserDefaults;
    [preferences setBool:YES forKey:@"JardDetectMeetings"];
    NSString *session = NSUUID.UUID.UUIDString.lowercaseString;
    __block NSUInteger reads = 0;
    JardMeetings *controller = [[JardMeetings alloc] initWithManaged:YES start:^(NSDictionary *options, BOOL prompt, JardDictationReply reply) {
        assert(false && "A managed meeting tried to capture audio");
    } send:^(NSString *command, NSDictionary *payload, JardDictationReply reply) {
        assert([command isEqual:@"jard.latestMeeting"]);
        reads++;
        reply(@{@"session": session}, nil);
    } changed:nil];
    [controller workspaceReady];
    assert(reads == 1 && controller.canPaste && !controller.keepsRunning);
    assert(![controller valueForKey:@"timer"] && ![controller valueForKey:@"calendar"]);
    NSString *message = @"Meetings are managed by your organization.";
    [controller request:@{@"action": @"status"} reply:^(id result, NSString *error) {
        assert(!error && [result[@"managed"] boolValue] && ![result[@"detection"] boolValue]);
        assert([result[@"message"] isEqual:message] && [result[@"hasText"] boolValue]);
    }];
    for (NSString *action in @[@"start", @"calendarConnect", @"detection", @"refreshRecovery", @"recoverMeeting"]) {
        [controller request:@{@"action": action, @"enabled": @NO} reply:^(id result, NSString *error) {
            assert(!result && [error isEqual:message]);
        }];
    }
    [controller startWithEstablishedPermissionsTitle:@"Managed test" reply:^(id result, NSString *error) {
        assert(!result && [error isEqual:message]);
    }];
    assert(reads == 1 && [preferences boolForKey:@"JardDetectMeetings"]);
    [controller shutdown];
    JardMeetings *reopened = [[JardMeetings alloc] initWithManaged:NO start:nil send:nil changed:nil];
    [reopened request:@{@"action": @"status"} reply:^(id result, NSString *error) {
        assert(!error && ![result[@"managed"] boolValue] && [result[@"detection"] boolValue]);
    }];
    [reopened shutdown];
}

int main(void) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSScreen *screen = NSScreen.mainScreen;
        assert(screen);
        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        NSArray *keys = @[@"JardShowMeetingIndicator", @"JardMeetingIndicatorCorner", @"JardMeetingIndicatorDisplay",
                         @"JardLastMeetingSession", @"JardDetectMeetings"];
        NSMutableDictionary *previous = NSMutableDictionary.dictionary;
        for (NSString *key in keys) {
            if ([defaults objectForKey:key]) previous[key] = [defaults objectForKey:key];
            [defaults removeObjectForKey:key];
        }
        testPlacement(screen);
        testMeetingWindow();
        testTitledMeeting();
        testManagedMeetings();
        puts("The capsule kept an explicit title through permission retry, duplicate Start, and completion.");
        for (NSString *key in keys) [defaults setObject:previous[key] forKey:key];
        puts("Native meeting panel passed four corners, Reduced Motion drag and cancel, permission retry, accessibility labels, hidden stop, and recovery.");
        puts("Managed Meetings skipped permissions, Calendar, detection and recovery. Relaunch restored the saved detection choice.");
    }
}
