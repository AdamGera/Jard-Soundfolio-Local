// Run with a two-second black MP4 and an unused output path.
#import "../jard_annotation_ink.h"
#import "../jard_annotation_region.h"
#import "../jard_annotation.h"
#import "../jard_annotation_keys.h"
#import "../jard_phone_annotation.h"
#import <assert.h>
#import <Carbon/Carbon.h>
#import <ImageIO/ImageIO.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>

@interface JardAnnotation (KeyboardFixture)
@property(nonatomic, copy) NSString *identifier;
@property(nonatomic, copy) NSString *source;
@property(nonatomic, strong) SCStream *stream;
@property(nonatomic, strong) SCRecordingOutput *recording;
@property(nonatomic, strong) NSURL *directory;
@property(nonatomic, strong) NSURL *movie;
@property(nonatomic) BOOL leased;
@property(nonatomic, strong, readonly) NSPanel *overlay;
@property(nonatomic, strong, readonly) NSView *ink;
- (void)preparePanels:(NSScreen *)screen target:(NSRect)target;
- (void)state:(NSString *)phase message:(NSString *)message;
- (void)recordingOutputDidFinishRecording:(SCRecordingOutput *)output;
- (void)submitAnnotation;
@end

@interface AnnotationStreamFixture : NSObject
@property(nonatomic) NSUInteger stops;
@property(nonatomic, copy) void (^completion)(NSError *error);
- (void)stopCaptureWithCompletionHandler:(void (^)(NSError *error))completion;
@end
@implementation AnnotationStreamFixture
- (void)stopCaptureWithCompletionHandler:(void (^)(NSError *error))completion {
    self.stops++;
    self.completion = completion;
}
@end

static void testAnnotationControls(void) {
    __block JardDictationReply leaseReply, importReply;
    __block NSString *released;
    __block NSUInteger imports = 0, releases = 0;
    JardAnnotation *annotation = [[JardAnnotation alloc] initWithSend:^(NSString *command, NSDictionary *payload, JardDictationReply reply) {
        if ([command isEqual:@"jard.annotationLease"]) {
            if ([payload[@"active"] boolValue]) leaseReply = [reply copy];
            else { released = payload[@"id"]; releases++; }
        } else {
            assert([command isEqual:@"jard.importAnnotation"]);
            imports++;
            importReply = [reply copy];
        }
    } stateChanged:nil];
    assert(![annotation cancel] && ![annotation stop]);
    __block BOOL cancelledStart = NO;
    [annotation start:@[] source:@"display" microphone:nil origin:nil reply:^(id result, NSString *error) {
        assert(!result && [error containsString:@"cancelled"]);
        cancelledStart = YES;
    }];
    NSString *pending = [annotation status][@"capture"];
    assert([[NSUUID alloc] initWithUUIDString:pending] && [annotation cancel] && annotation.busy);
    assert([[annotation status][@"phase"] isEqual:@"cancelling"] && releases == 0);
    leaseReply(@{@"active": @YES}, nil);
    leaseReply = nil;
    assert(cancelledStart && !annotation.busy && releases == 1 && [released isEqual:pending]);
    assert([[annotation status][@"last"][@"id"] isEqual:pending]);

    NSURL *folder = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString] isDirectory:YES];
    assert([NSFileManager.defaultManager createDirectoryAtURL:folder withIntermediateDirectories:NO attributes:nil error:nil]);
    NSURL *movie = [folder URLByAppendingPathComponent:@"Screen annotation.mp4"];
    assert([@"disposable fixture" writeToURL:movie atomically:YES encoding:NSUTF8StringEncoding error:nil]);
    AnnotationStreamFixture *stream = AnnotationStreamFixture.new;
    SCRecordingOutput *output = (id)NSObject.new;
    annotation.identifier = @"stream-cancel";
    annotation.source = @"window";
    annotation.stream = (id)stream;
    annotation.recording = output;
    annotation.leased = YES;
    annotation.directory = folder;
    annotation.movie = movie;
    [annotation state:@"recording" message:@""];
    assert([annotation cancel] && annotation.busy);
    assert([[annotation status][@"phase"] isEqual:@"cancelling"] && [[annotation status][@"capture"] isEqual:@"stream-cancel"]);
    assert(![annotation cancel] && ![annotation stop] && stream.stops == 1 && releases == 1);
    [annotation recordingOutputDidFinishRecording:output];
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    assert(!imports && [NSFileManager.defaultManager fileExistsAtPath:movie.path]);
    stream.completion(nil);
    stream.completion = nil;
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    NSDictionary *cancelled = [annotation status];
    assert(!annotation.busy && stream.stops == 1 && releases == 2 && [released isEqual:@"stream-cancel"]);
    assert([cancelled[@"phase"] isEqual:@"cancelled"] && [cancelled[@"last"][@"source"] isEqual:@"window"]);
    assert(![NSFileManager.defaultManager fileExistsAtPath:folder.path] && imports == 0);

    annotation.identifier = @"stream-save";
    annotation.source = @"display";
    annotation.stream = (id)(stream = AnnotationStreamFixture.new);
    annotation.leased = YES;
    annotation.movie = movie;
    [annotation state:@"recording" message:@""];
    assert([annotation stop] && [[annotation status][@"phase"] isEqual:@"saving"] && stream.stops == 1);
    assert(![annotation stop] && ![annotation cancel]);
    [annotation submitAnnotation];
    assert(imports == 1 && annotation.busy);
    importReply(@{@"id": @"stream-save"}, nil);
    importReply = nil;
    NSDictionary *finished = [annotation status];
    assert(!annotation.busy && releases == 3 && [released isEqual:@"stream-save"]);
    assert([finished[@"session"] isEqual:@"stream-save"] && [finished[@"last"][@"id"] isEqual:finished[@"session"]]);
    assert([finished[@"last"][@"phase"] isEqual:@"finished"] && ![annotation cancel]);
    puts("Annotation status, late leases, idempotent stop, discard, and saved-session identity passed.");
}

static NSEvent *mouseEvent(NSEventType type, NSPoint location, NSPanel *panel);

static const CGKeyCode annotationFixtureCode = kVK_ANSI_O;

static BOOL annotationKey(JardAnnotation *annotation, CGEventType type, CGKeyCode code, CGEventFlags flags, BOOL allowStart,
                          dispatch_block_t start) {
    CGEventRef event = CGEventCreateKeyboardEvent(NULL, code, type != kCGEventKeyUp);
    CGEventSetFlags(event, flags);
    BOOL consumed = [annotation handleEvent:type event:event shortcutCode:annotationFixtureCode allowStart:allowStart start:start];
    CFRelease(event);
    return consumed;
}

static void movePen(JardAnnotation *annotation, CGFloat x, CGFloat y) {
    CGFloat height = CGRectGetHeight(CGDisplayBounds(CGMainDisplayID()));
    CGEventRef event = CGEventCreateMouseEvent(NULL, kCGEventMouseMoved, CGPointMake(x, height - y), kCGMouseButtonLeft);
    assert(![annotation handleEvent:kCGEventMouseMoved event:event shortcutCode:annotationFixtureCode allowStart:NO start:nil]);
    CFRelease(event);
}

static NSArray *inkEvents(JardAnnotation *annotation) { return [annotation.ink valueForKey:@"events"]; }

static void testAnnotationKeys(void) {
    const CGEventFlags chord = kCGEventFlagMaskAlternate | kCGEventFlagMaskShift;
    const CGEventFlags shift = kCGEventFlagMaskShift;
    JardAnnotationKeys keys = {0};
    assert(!JardAnnotationKey(&keys, JardAnnotationRecording, JardAnnotationChordDown, 1.1, 1, JardAnnotationChord, true, false));
    assert(JardAnnotationKey(&keys, JardAnnotationRecording, JardAnnotationChordDown, 1.2, 1, JardAnnotationChord, false, false) == JardAnnotationTake);
    assert(JardAnnotationKey(&keys, JardAnnotationRecording, JardAnnotationChordUp, 1.3, 1, JardAnnotationChord, false, false) == JardAnnotationTake);
    assert(JardAnnotationKey(&keys, JardAnnotationRecording, JardAnnotationChordDown, 2, 1, JardAnnotationChord, false, false) ==
        (JardAnnotationTake | JardAnnotationArmDraw));
    assert(JardAnnotationKey(&keys, JardAnnotationRecording, JardAnnotationModifiers, 2.2, 1, JardAnnotationShift, false, false) ==
        JardAnnotationSave);
    assert(!keys.shiftPen && !keys.heldPen);
    assert(JardAnnotationKey(&keys, JardAnnotationUnavailable, JardAnnotationChordUp, 2.3, 1, JardAnnotationShift, false, false) ==
        JardAnnotationTake);

    JardAnnotation *annotation = [[JardAnnotation alloc] initWithSend:^(NSString *command, NSDictionary *payload, JardDictationReply reply) {
        assert(false && "Synthetic input must not request a capture lease itself.");
    } stateChanged:nil];
    __block NSUInteger starts = 0;
    dispatch_block_t start = ^{ starts++; };
    assert(!annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, NO, start));
    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, YES, start));
    assert(annotationKey(annotation, kCGEventKeyUp, annotationFixtureCode, chord, YES, start));
    assert(starts == 0);
    assert(!annotationKey(annotation, kCGEventFlagsChanged, kVK_Option, shift, YES, start));
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    assert(starts == 1);
    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, YES, start));
    assert(!annotationKey(annotation, kCGEventFlagsChanged, kVK_Shift, 0, NO, start));
    assert(annotationKey(annotation, kCGEventKeyUp, annotationFixtureCode, 0, NO, start));
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    assert(starts == 1);

    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, YES, start));
    assert(annotationKey(annotation, kCGEventKeyDown, kVK_Escape, chord, YES, start));
    assert(annotationKey(annotation, kCGEventKeyUp, kVK_Escape, chord, YES, start));
    assert(annotationKey(annotation, kCGEventKeyUp, annotationFixtureCode, chord, YES, start));
    assert(!annotationKey(annotation, kCGEventFlagsChanged, kVK_Shift, 0, YES, start));
    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, YES, start));
    assert(![annotation handleEvent:kCGEventTapDisabledByTimeout event:NULL shortcutCode:annotationFixtureCode allowStart:NO start:start]);
    assert(!annotationKey(annotation, kCGEventFlagsChanged, kVK_Shift, 0, YES, start));
    assert(!annotationKey(annotation, kCGEventKeyUp, annotationFixtureCode, 0, YES, start));
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    assert(starts == 1);
    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, YES, start));
    assert(!annotationKey(annotation, kCGEventFlagsChanged, kVK_Shift, 0, YES, start));
    assert(![annotation handleEvent:kCGEventTapDisabledByTimeout event:NULL shortcutCode:annotationFixtureCode allowStart:NO start:start]);
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    assert(starts == 1);

    annotation.identifier = @"fixture-picker";
    [annotation state:@"picking" message:@""];
    assert(annotationKey(annotation, kCGEventKeyDown, kVK_Escape, 0, YES, start));
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    assert(!annotation.busy);
    assert(annotationKey(annotation, kCGEventKeyUp, kVK_Escape, 0, YES, start));

    NSScreen *screen = NSScreen.mainScreen;
    NSRect bounds = NSMakeRect(NSMidX(screen.frame) - 150, NSMidY(screen.frame) - 150, 300, 180);
    annotation.identifier = @"gesture-fixture";
    [annotation preparePanels:screen target:bounds];
    [annotation.ink setValue:@(NSProcessInfo.processInfo.systemUptime - 1) forKey:@"startTime"];
    [annotation state:@"recording" message:@""];
    [annotation.overlay orderFrontRegardless];
    assert(!annotationKey(annotation, kCGEventKeyDown, kVK_Escape, 0, NO, start));
    assert(annotation.overlay.ignoresMouseEvents);
    assert(!annotationKey(annotation, kCGEventFlagsChanged, kVK_Shift, shift, NO, start));
    assert(!annotation.overlay.ignoresMouseEvents);
    [annotation.ink mouseDown:mouseEvent(NSEventTypeLeftMouseDown, NSMakePoint(60, 36), annotation.overlay)];
    [annotation.ink mouseDragged:mouseEvent(NSEventTypeLeftMouseDragged, NSMakePoint(90, 54), annotation.overlay)];
    [annotation.ink mouseUp:mouseEvent(NSEventTypeLeftMouseUp, NSMakePoint(90, 54), annotation.overlay)];
    assert(inkEvents(annotation).count == 1 && NSEqualPoints([inkEvents(annotation).lastObject[@"to"] pointValue], NSMakePoint(.3, .3)));
    assert(annotationKey(annotation, kCGEventKeyDown, kVK_Escape, shift, NO, start));
    assert(annotationKey(annotation, kCGEventKeyUp, kVK_Escape, shift, NO, start));
    assert(annotation.overlay.ignoresMouseEvents && annotation.busy);
    assert(!annotationKey(annotation, kCGEventFlagsChanged, kVK_Shift, 0, NO, start));

    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, NO, start));
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.3]];
    assert(!annotation.overlay.ignoresMouseEvents);
    movePen(annotation, NSMinX(bounds) - 5, NSMinY(bounds) - 5);
    movePen(annotation, NSMinX(bounds) + 75, NSMinY(bounds) + 45);
    movePen(annotation, NSMinX(bounds) + 150, NSMinY(bounds) + 90);
    NSDictionary *first = inkEvents(annotation).lastObject;
    assert(NSEqualPoints([first[@"from"] pointValue], NSMakePoint(.25, .25)));
    assert(NSEqualPoints([first[@"to"] pointValue], NSMakePoint(.5, .5)));
    NSUInteger count = inkEvents(annotation).count;
    movePen(annotation, NSMaxX(bounds) + 5, NSMaxY(bounds) + 5);
    movePen(annotation, NSMinX(bounds) + 15, NSMinY(bounds) + 9);
    assert(inkEvents(annotation).count == count);
    movePen(annotation, NSMinX(bounds) + 27, NSMinY(bounds) + 18);
    assert(inkEvents(annotation).count == count + 1 && ![first[@"stroke"] isEqual:inkEvents(annotation).lastObject[@"stroke"]]);
    assert(annotationKey(annotation, kCGEventKeyUp, annotationFixtureCode, chord, NO, start));
    assert(annotation.overlay.ignoresMouseEvents && [[annotation status][@"phase"] isEqual:@"recording"]);

    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, NO, start));
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.3]];
    assert(!annotation.overlay.ignoresMouseEvents);
    assert(![annotation handleEvent:kCGEventTapDisabledByTimeout event:NULL shortcutCode:annotationFixtureCode allowStart:NO start:nil]);
    assert(annotation.overlay.ignoresMouseEvents && [[annotation status][@"phase"] isEqual:@"recording"]);
    assert(!annotationKey(annotation, kCGEventKeyUp, annotationFixtureCode, 0, NO, start));

    assert(annotationKey(annotation, kCGEventKeyDown, annotationFixtureCode, chord, NO, start));
    assert(annotationKey(annotation, kCGEventKeyUp, annotationFixtureCode, chord, NO, start));
    [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.03]];
    assert([[annotation status][@"phase"] isEqual:@"saving"] && !annotation.overlay.visible);
    [annotation shutdown];
    puts("Annotation shortcut tap, hold, Shift drag, Escape, target bounds, and tap interruption passed.");
}

static NSEvent *mouseEvent(NSEventType type, NSPoint location, NSPanel *panel) {
    return [NSEvent mouseEventWithType:type location:location modifierFlags:0 timestamp:0
        windowNumber:panel.windowNumber context:nil eventNumber:0 clickCount:1 pressure:1];
}

static void testAreaSelection(void) {
    NSRect bounds = NSMakeRect(0, 0, 1440, 900);
    NSRect drag = JardAnnotationDragRect(NSMakePoint(900, 300), NSMakePoint(-20, 970), bounds);
    assert(NSEqualRects(drag, NSMakeRect(0, 300, 900, 600)));
    NSRect display = NSMakeRect(-1440, 900, 1440, 900);
    NSRect area = NSOffsetRect(drag, display.origin.x, display.origin.y);
    assert(CGRectEqualToRect(JardAnnotationDisplaySourceRect(area, display), CGRectMake(0, 0, 900, 600)));
    assert(CGRectEqualToRect(JardAnnotationDisplaySourceRect(NSMakeRect(-100, 1000, 200, 300), display),
                             CGRectMake(1340, 500, 100, 300)));
    assert(CGRectIsEmpty(JardAnnotationDisplaySourceRect(NSMakeRect(50, 50, 100, 100), display)));
    assert(NSEqualRects(JardAnnotationControlsRect(bounds, NSZeroRect), NSMakeRect(574, 24, 292, 46)));
    NSRect lowerHalf = NSMakeRect(0, 0, 1440, 450);
    assert(NSIsEmptyRect(NSIntersectionRect(lowerHalf, JardAnnotationControlsRect(bounds, lowerHalf))));

    [NSApplication sharedApplication];
    NSScreen *screen = NSScreen.mainScreen;
    assert(screen);
    __block NSRect selected = NSZeroRect;
    __block NSUInteger selections = 0;
    NSPanel *panel = JardAnnotationRegionPanel(screen, ^(NSRect result) { selected = result; selections++; });
    NSView *view = panel.contentView;
    [view mouseDown:mouseEvent(NSEventTypeLeftMouseDown, NSMakePoint(200, 200), panel)];
    [view mouseUp:mouseEvent(NSEventTypeLeftMouseUp, NSMakePoint(201, 204), panel)];
    assert(selections == 0);
    [view mouseDown:mouseEvent(NSEventTypeLeftMouseDown, NSMakePoint(220, 260), panel)];
    [view mouseDragged:mouseEvent(NSEventTypeLeftMouseDragged, NSMakePoint(90, 160), panel)];
    [view mouseUp:mouseEvent(NSEventTypeLeftMouseUp, NSMakePoint(90, 160), panel)];
    assert(selections == 1 && NSEqualRects(selected, NSOffsetRect(NSMakeRect(90, 160, 130, 100),
                                                                 NSMinX(screen.frame), NSMinY(screen.frame))));
    NSEvent *escape = [NSEvent keyEventWithType:NSEventTypeKeyDown location:NSZeroPoint modifierFlags:0 timestamp:0
        windowNumber:panel.windowNumber context:nil characters:@"" charactersIgnoringModifiers:@"" isARepeat:NO keyCode:53];
    [view keyDown:escape];
    assert(selections == 2 && NSIsEmptyRect(selected));
    puts("Area bounds, display-local crop, short drag and Escape passed.");
}

static void testPhonePicture(void) {
    NSRect bounds = NSMakeRect(10, 20, 300, 500);
    assert(NSEqualRects(JardPhonePictureRect(NSMakeSize(1200, 2400), bounds), NSMakeRect(35, 20, 250, 500)));
    assert(NSEqualRects(JardPhonePictureRect(NSMakeSize(2400, 1200), bounds), NSMakeRect(10, 195, 300, 150)));
    assert(NSIsEmptyRect(JardPhonePictureRect(NSZeroSize, bounds)));
    assert(NSIsEmptyRect(JardPhonePictureRect(NSMakeSize(1, 1), NSZeroRect)));
    puts("Wired screen ink bounds passed for portrait and landscape pictures.");
}

static void testRoutingOrigin(void) {
    NSDictionary *browser = @{@"app": @"com.fixture.Browser", @"name": @"Jard"};
    NSDictionary *site = @{@"app": @"com.fixture.Browser", @"name": @"Jard", @"site": @"example.test"};
    CGRect frame = CGRectMake(20, 40, 600, 420);
    NSDictionary *sample = @{@"pid": @42, @"app": site, @"title": @"Example",
        @"bounds": [NSValue valueWithRect:frame]};
    assert([JardAnnotationRoutingOrigin(sample, browser, 42, @"Example", frame) isEqual:site]);
    assert([JardAnnotationRoutingOrigin(sample, browser, 42, @"Different tab", frame) isEqual:browser]);
    assert([JardAnnotationRoutingOrigin(sample, browser, 41, @"Example", frame) isEqual:browser]);
    assert([JardAnnotationRoutingOrigin(nil, browser, 41, @"Window", frame) isEqual:browser]);
    assert([JardAnnotationRoutingOrigin(sample, nil, 0, nil, CGRectMake(0, 0, 700, 500)) isEqual:site]);
    assert(!JardAnnotationRoutingOrigin(sample, nil, 0, nil, CGRectMake(900, 0, 700, 500)));
    NSDictionary *terminal = @{@"app": @"com.apple.Terminal", @"name": @"Terminal"};
    NSDictionary *context = @{@"folder": @"/fixture/project/sub", @"project": @"/fixture/project"};
    NSDictionary *terminalSample = @{@"pid": @43, @"title": @"Project", @"bounds": [NSValue valueWithRect:frame],
        @"app": @{@"terminal": context}};
    assert([JardAnnotationRoutingOrigin(terminalSample, terminal, 43, @"Project", frame)[@"terminal"] isEqual:context]);
    assert(!JardAnnotationRoutingOrigin(terminalSample, terminal, 43, @"Another project", frame)[@"terminal"]);
    puts("Annotation origin matched the selected app, the exact browser window, and the captured display.");
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        assert(argc == 3);
        __block BOOL checkedLease = NO;
        JardAnnotation *annotation = [[JardAnnotation alloc] initWithSend:^(NSString *command, NSDictionary *payload,
                JardDictationReply reply) {
            assert([command isEqual:@"jard.annotationLease"]);
            NSString *identifier = payload[@"id"];
            assert([[NSUUID alloc] initWithUUIDString:identifier] && [identifier isEqual:identifier.lowercaseString]);
            checkedLease = YES;
            reply(nil, @"Stop the fixture before requesting permissions.");
        } stateChanged:nil];
        [annotation start:@[] source:@"window" microphone:nil origin:nil reply:^(id result, NSString *error) { assert(!result && error); }];
        assert(checkedLease && !annotation.busy);
        NSRect primary = JardAnnotationAppKitRect(CGRectMake(40, 60, 800, 400), 900);
        assert(NSEqualRects(primary, NSMakeRect(40, 440, 800, 400)));
        NSRect above = JardAnnotationAppKitRect(CGRectMake(-800, -200, 800, 100), 900);
        assert(NSEqualRects(above, NSMakeRect(-800, 1000, 800, 100)));
        assert(NSEqualPoints(JardAnnotationUnitPoint(NSMakePoint(100, 30), NSMakeSize(400, 120)), NSMakePoint(.25, .25)));
        assert(NSEqualPoints(JardAnnotationUnitPoint(NSMakePoint(-10, 200), NSMakeSize(400, 120)), NSMakePoint(0, 1)));
        testPhonePicture();
        testRoutingOrigin();
        testAreaSelection();
        testAnnotationKeys();
        testAnnotationControls();

        NSArray *events = @[
            JardAnnotationEdge(NSMakePoint(.15, .25), NSMakePoint(.55, .25), .5),
            @{@"clear": @YES, @"time": @1.1},
            JardAnnotationEdge(NSMakePoint(.52, .72), NSMakePoint(.85, .72), 1.5),
        ];
        __block BOOL finished = NO;
        __block NSError *failure = nil;
        JardRenderAnnotation([NSURL fileURLWithPath:@(argv[1])], [NSURL fileURLWithPath:@(argv[2])],
                                   events, ^(NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{ failure = error; finished = YES; });
        });
        NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:30];
        while (!finished && deadline.timeIntervalSinceNow > 0)
            [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.02]];
        if (failure) NSLog(@"%@", failure);
        assert(finished && !failure);
        NSArray *planned = JardAnnotationFrameTimes(events, 2);
        assert(planned.count >= 3 && planned.count <= 6);
        assert([planned[1] doubleValue] > .5 && [planned[1] doubleValue] < .72);
        finished = NO;
        NSURL *movie = [NSURL fileURLWithPath:@(argv[2])];
        NSString *source = [[@(argv[1]) pathExtension] isEqual:@"mov"] ? @"phone" : @"window";
        NSDictionary *origin = [source isEqual:@"window"]
            ? @{@"app": @"com.fixture.Browser", @"name": @"Jard", @"site": @"example.test"} : nil;
        JardWriteAnnotationAssets(movie, source, origin, events, ^(NSError *error) { failure = error; finished = YES; });
        deadline = [NSDate dateWithTimeIntervalSinceNow:25];
        while (!finished && deadline.timeIntervalSinceNow > 0)
            [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.02]];
        assert(finished && !failure);
        NSURL *folder = movie.URLByDeletingLastPathComponent;
        NSData *json = [NSData dataWithContentsOfURL:[folder URLByAppendingPathComponent:@"annotation.json"]];
        NSDictionary *manifest = [NSJSONSerialization JSONObjectWithData:json options:0 error:nil];
        assert([manifest[@"source"] isEqual:source] && [manifest[@"version"] isEqual:@1]);
        assert(origin ? [manifest[@"origin"] isEqual:origin] : !manifest[@"origin"]);
        assert([manifest[@"events"] count] == 3 && [manifest[@"frames"] count] >= 2);
        assert(([manifest[@"events"][0][@"from"] isEqual:@[@.15, @.25]] && [manifest[@"events"][1][@"clear"] boolValue]));
        NSURL *preview = [folder URLByAppendingPathComponent:@"contact-sheet.png"];
        CGImageSourceRef images = CGImageSourceCreateWithURL((__bridge CFURLRef)preview, NULL);
        assert(images);
        CGImageRef sheet = CGImageSourceCreateImageAtIndex(images, 0, NULL);
        assert(sheet && CGImageGetWidth(sheet) == 872 && CGImageGetHeight(sheet) >= 282);
        CGImageRelease(sheet);
        CFRelease(images);
        NSPasteboard *board = [NSPasteboard pasteboardWithUniqueName];
        NSString *markdown = @"# Screen annotation\n\n## Narration\nReview the chart.\n";
        NSData *png = [NSData dataWithContentsOfURL:preview];
        BOOL copiedImage = NO;
        assert(JardCopyAnnotationHandoff(board, board.changeCount, markdown, png, &copiedImage) && copiedImage);
        assert([[board stringForType:NSPasteboardTypeString] isEqual:markdown]);
        NSData *rtfd = [board dataForType:NSPasteboardTypeRTFD];
        NSAttributedString *rich = [[NSAttributedString alloc] initWithRTFD:rtfd documentAttributes:nil];
        NSTextAttachment *attachment = [rich attribute:NSAttachmentAttributeName atIndex:0 effectiveRange:nil];
        assert([attachment.fileWrapper.preferredFilename isEqual:@"contact-sheet.png"]);
        assert([attachment.fileWrapper.regularFileContents isEqual:png] && [rich.string containsString:markdown]);
        NSTextView *destination = [[NSTextView alloc] initWithFrame:NSMakeRect(0, 0, 800, 500)];
        destination.richText = destination.importsGraphics = YES;
        assert([destination readSelectionFromPasteboard:board type:NSPasteboardTypeRTFD]);
        assert([destination.string containsString:markdown] && [destination.textStorage attribute:NSAttachmentAttributeName atIndex:0 effectiveRange:nil]);
        assert(JardCopyAnnotationHandoff(board, board.changeCount, markdown, nil, &copiedImage) && !copiedImage);
        assert([[board stringForType:NSPasteboardTypeString] isEqual:markdown] && ![board dataForType:NSPasteboardTypeRTFD]);
        NSInteger staleClipboard = board.changeCount;
        [board clearContents];
        [board setString:@"New clipboard content" forType:NSPasteboardTypeString];
        assert(!JardCopyAnnotationHandoff(board, staleClipboard, markdown, png, &copiedImage));
        assert(!copiedImage && [[board stringForType:NSPasteboardTypeString] isEqual:@"New clipboard content"]);
        [board releaseGlobally];
        puts("Annotation clipboard handoff preserved Markdown and the contact sheet in a private pasteboard fixture.");
        puts("Window geometry, timed ink, restored stroke data and the native contact sheet passed.");
    }
}
