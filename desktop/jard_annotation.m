#import "jard_annotation.h"
#import "jard_annotation_ink.h"
#import "jard_annotation_keys.h"
#import "jard_annotation_region.h"
#import "jard_phone_annotation.h"
#import <AVFoundation/AVFoundation.h>
#import <Carbon/Carbon.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>

BOOL JardCopyAnnotationHandoff(NSPasteboard *board, NSInteger expectedChangeCount, NSString *markdown, NSData *png, BOOL *includedImage) {
    if (includedImage) *includedImage = NO;
    if (!markdown.length) return NO;
    NSPasteboardItem *item = NSPasteboardItem.new;
    if (![item setString:markdown forType:NSPasteboardTypeString]) return NO;
    BOOL rich = NO;
    if (png.length && [[NSImage alloc] initWithData:png]) {
        NSFileWrapper *file = [[NSFileWrapper alloc] initRegularFileWithContents:png];
        file.preferredFilename = @"contact-sheet.png";
        NSTextAttachment *attachment = [[NSTextAttachment alloc] initWithFileWrapper:file];
        NSMutableAttributedString *contents = [[NSMutableAttributedString alloc]
            initWithAttributedString:[NSAttributedString attributedStringWithAttachment:attachment]];
        [contents appendAttributedString:[[NSAttributedString alloc] initWithString:[@"\n\n" stringByAppendingString:markdown]]];
        NSData *rtfd = [contents RTFDFromRange:NSMakeRange(0, contents.length) documentAttributes:@{}];
        if (rtfd) rich = [item setData:rtfd forType:NSPasteboardTypeRTFD];
    }
    if (board.changeCount != expectedChangeCount) return NO;
    [board clearContents];
    BOOL copied = [board writeObjects:@[item]];
    if (includedImage) *includedImage = copied && rich;
    return copied;
}

@interface JardInkView : NSView
@property(nonatomic, strong) NSMutableArray<NSBezierPath *> *strokes;
@property(nonatomic, strong) NSMutableArray<NSDictionary *> *events;
@property(nonatomic) BOOL renderedInk;
@property(nonatomic) NSUInteger stroke;
@property(nonatomic) NSPoint lastPoint;
@property(nonatomic) NSTimeInterval startTime;
@property(nonatomic) NSUInteger visibleFrom;
@property(nonatomic) BOOL strokeActive, followsPointer;
- (void)beginStrokeAt:(NSPoint)point;
- (void)continueStrokeAt:(NSPoint)point;
- (void)endStroke;
- (void)clear;
@end
@implementation JardInkView
- (instancetype)initWithFrame:(NSRect)frame {
    if (!(self = [super initWithFrame:frame])) return nil;
    _strokes = NSMutableArray.array;
    _events = NSMutableArray.array;
    return self;
}
- (BOOL)acceptsFirstMouse:(NSEvent *)event { return YES; }
- (BOOL)isOpaque { return NO; }
- (void)mouseDown:(NSEvent *)event {
    if (!self.followsPointer) [self beginStrokeAt:[self convertPoint:event.locationInWindow fromView:nil]];
}
- (void)mouseDragged:(NSEvent *)event {
    if (!self.followsPointer) [self continueStrokeAt:[self convertPoint:event.locationInWindow fromView:nil]];
}
- (void)mouseUp:(NSEvent *)event { if (!self.followsPointer) [self endStroke]; }
- (void)beginStrokeAt:(NSPoint)point {
    self.strokeActive = YES;
    self.lastPoint = JardAnnotationUnitPoint(point, self.bounds.size);
    self.stroke++;
    if (self.renderedInk) return;
    NSBezierPath *path = NSBezierPath.bezierPath;
    path.lineWidth = 5;
    path.lineCapStyle = NSLineCapStyleRound;
    path.lineJoinStyle = NSLineJoinStyleRound;
    [path moveToPoint:point];
    [self.strokes addObject:path];
    if (self.strokes.count > 120) [self.strokes removeObjectAtIndex:0];
}
- (void)continueStrokeAt:(NSPoint)point {
    if (!self.strokeActive) return;
    NSPoint normalized = JardAnnotationUnitPoint(point, self.bounds.size);
    if (self.startTime > 0 && self.events.count < 100000) {
        NSMutableDictionary *edge = [JardAnnotationEdge(self.lastPoint, normalized,
            MAX(0, NSProcessInfo.processInfo.systemUptime - self.startTime)) mutableCopy];
        edge[@"stroke"] = @(self.stroke);
        [self.events addObject:edge];
    }
    self.lastPoint = normalized;
    if (!self.renderedInk) [self.strokes.lastObject lineToPoint:point];
    self.needsDisplay = YES;
}
- (void)endStroke { self.strokeActive = NO; }
- (void)clear {
    [self endStroke];
    [self.strokes removeAllObjects];
    if (self.events.count > self.visibleFrom) {
        if (self.events.count < 100000) {
            [self.events addObject:@{@"clear": @YES, @"time": @(MAX(0, NSProcessInfo.processInfo.systemUptime - self.startTime))}];
        }
        self.visibleFrom = self.events.count;
    }
    self.needsDisplay = YES;
}
- (void)drawRect:(NSRect)dirtyRect {
    [NSGraphicsContext saveGraphicsState];
    [[NSColor colorWithSRGBRed:1 green:.4 blue:.22 alpha:1] setStroke];
    if (self.renderedInk) {
        NSBezierPath *path = NSBezierPath.bezierPath;
        path.lineCapStyle = NSLineCapStyleRound;
        path.lineWidth = MAX(3, MIN(NSWidth(self.bounds), NSHeight(self.bounds)) * .007);
        for (NSUInteger i = self.visibleFrom; i < self.events.count; i++) {
            NSPoint from = [self.events[i][@"from"] pointValue], to = [self.events[i][@"to"] pointValue];
            [path moveToPoint:NSMakePoint(from.x * NSWidth(self.bounds), from.y * NSHeight(self.bounds))];
            [path lineToPoint:NSMakePoint(to.x * NSWidth(self.bounds), to.y * NSHeight(self.bounds))];
        }
        [path stroke];
    } else {
        for (NSBezierPath *path in self.strokes) [path stroke];
    }
    [NSGraphicsContext restoreGraphicsState];
}
- (void)resetCursorRects { [self addCursorRect:self.bounds cursor:NSCursor.crosshairCursor]; }
@end

@interface JardAnnotationPanel : NSPanel
@end
@implementation JardAnnotationPanel
- (BOOL)canBecomeKeyWindow { return NO; }
- (BOOL)canBecomeMainWindow { return NO; }
@end

@interface JardAnnotationPicker : NSObject <SCContentSharingPickerObserver>
@property(nonatomic, copy) void (^select)(SCContentFilter *filter);
@property(nonatomic, copy) void (^finish)(NSError *error);
@end

@implementation JardAnnotationPicker
- (void)contentSharingPicker:(SCContentSharingPicker *)picker didCancelForStream:(SCStream *)stream { self.finish(nil); }
- (void)contentSharingPickerStartDidFailWithError:(NSError *)error { self.finish(error); }
- (void)contentSharingPicker:(SCContentSharingPicker *)picker didUpdateWithFilter:(SCContentFilter *)filter
    forStream:(SCStream *)stream { self.select(filter); }
@end

@interface JardAnnotation () <SCStreamDelegate, SCRecordingOutputDelegate>
@property(nonatomic, copy) JardDictationSend send;
@property(nonatomic, copy) void (^changed)(NSDictionary *);
@property(nonatomic, copy) NSString *phase, *message, *identifier, *sessionID;
@property(nonatomic, copy) NSString *source, *microphone;
@property(nonatomic, copy) NSDictionary *originSnapshot, *routingOrigin;
@property(nonatomic, strong) JardPhoneAnnotation *phone;
@property(nonatomic, copy) NSArray<NSString *> *languages;
@property(nonatomic, copy) NSDictionary *lastOutcome;
@property(nonatomic, strong) NSNumber *stoppedAfter;
@property(nonatomic, strong) NSURL *directory, *movie, *recovery;
@property(nonatomic, strong) SCStream *stream;
@property(nonatomic, strong) SCRecordingOutput *recording;
@property(nonatomic, strong) JardAnnotationPicker *pickerObserver;
@property(nonatomic, strong) JardAnnotationPanel *overlay, *controls;
@property(nonatomic, strong) NSPanel *regionPanel;
@property(nonatomic) NSRect captureArea;
@property(nonatomic, strong) JardInkView *ink;
@property(nonatomic, strong) NSButton *penButton;
@property(nonatomic, strong) NSTimer *windowTracker;
@property(nonatomic, strong) NSTimer *holdTimer;
@property(nonatomic) CGWindowID windowID;
@property(nonatomic) pid_t ownerPID;
@property(nonatomic) BOOL acquiringLease, leased, closed, penLatched;
@property(nonatomic) JardAnnotationKeys keys;
@property(nonatomic) NSUInteger keyboardGeneration;
@end

@implementation JardAnnotation

- (instancetype)initWithSend:(JardDictationSend)send stateChanged:(void (^)(NSDictionary *))changed {
    if (!(self = [super init])) return nil;
    _send = [send copy];
    _changed = [changed copy];
    _phase = @"idle";
    return self;
}

- (BOOL)busy { return self.identifier != nil; }

- (NSNumber *)elapsed {
    if (self.stoppedAfter) return self.stoppedAfter;
    return self.ink.startTime > 0 ? @(MAX(0, NSProcessInfo.processInfo.systemUptime - self.ink.startTime)) : nil;
}

- (NSDictionary *)status {
    return @{@"phase": self.phase, @"message": self.message ?: @"",
        @"session": self.sessionID ?: @"", @"recoverable": @(self.recovery != nil), @"busy": @(self.busy),
        @"capture": self.identifier ?: (id)NSNull.null, @"source": (self.busy ? self.source : nil) ?: (id)NSNull.null,
        @"elapsedSeconds": self.elapsed ?: (id)NSNull.null, @"last": self.lastOutcome ?: (id)NSNull.null};
}

- (void)state:(NSString *)phase message:(NSString *)message {
    self.phase = phase;
    self.message = message;
    if (self.changed) self.changed([self status]);
}

- (void)conclude:(NSString *)phase message:(NSString *)message {
    if (self.identifier) {
        self.lastOutcome = @{@"id": self.identifier, @"phase": phase, @"source": self.source ?: (id)NSNull.null,
            @"elapsedSeconds": self.elapsed ?: (id)NSNull.null, @"session": self.sessionID ?: (id)NSNull.null,
            @"message": message};
    }
    [self reset];
    [self state:phase message:message];
}

- (void)start:(NSArray<NSString *> *)languages source:(NSString *)source microphone:(NSString *)microphone origin:(NSDictionary *)origin reply:(JardDictationReply)reply {
    if (self.busy || self.closed) { reply(nil, @"An annotation is already active."); return; }
    self.identifier = NSUUID.UUID.UUIDString.lowercaseString;
    self.languages = languages;
    self.source = source;
    self.originSnapshot = origin;
    self.microphone = microphone;
    self.sessionID = nil;
    self.acquiringLease = YES;
    NSString *identifier = self.identifier;
    [self state:@"starting" message:@"Preparing screen annotation…"];
    self.send(@"jard.annotationLease", @{@"id": identifier, @"active": @YES}, ^(id result, NSString *error) {
        if (![self.identifier isEqual:identifier]) {
            if (!error) self.send(@"jard.annotationLease", @{@"id": identifier, @"active": @NO}, nil);
            reply(nil, @"Annotation was cancelled.");
            return;
        }
        self.acquiringLease = NO;
        if ([self.phase isEqual:@"cancelling"]) {
            self.leased = !error;
            [self conclude:@"cancelled" message:@"Annotation discarded."];
            reply(nil, @"Annotation was cancelled.");
            return;
        }
        if (error) { [self fail:error keepMovie:NO]; reply(nil, error); return; }
        self.leased = YES;
        if ([source isEqual:@"phone"] && !JardHasWiredAnnotationDevice()) {
            NSString *message = @"Connect an unlocked iPhone or iPad by USB to start an annotation.";
            [self fail:message keepMovie:NO];
            reply(nil, message);
            return;
        }
        if ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] == AVAuthorizationStatusNotDetermined) {
            reply([self status], nil);
            [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL allowed) {
                dispatch_async(dispatch_get_main_queue(), ^{ [self beginPicker:identifier reply:nil]; });
            }];
        } else {
            [self beginPicker:identifier reply:reply];
        }
    });
}

- (void)beginPicker:(NSString *)identifier reply:(JardDictationReply)reply {
    if (![self.identifier isEqual:identifier]) { if (reply) reply(nil, @"Annotation was cancelled."); return; }
    if ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio] != AVAuthorizationStatusAuthorized) {
        NSString *error = @"Allow microphone access in System Settings to record narration.";
        [self fail:error keepMovie:NO];
        if (reply) reply(nil, error);
        return;
    }
    if ([self.source isEqual:@"phone"]) {
        [self authorizePhone:identifier reply:reply];
        return;
    }
    SCContentSharingPicker *picker = SCContentSharingPicker.sharedPicker;
    SCContentSharingPickerConfiguration *configuration = SCContentSharingPickerConfiguration.new;
    BOOL window = [self.source isEqual:@"window"];
    configuration.allowedPickerModes = window ? SCContentSharingPickerModeSingleWindow : SCContentSharingPickerModeSingleDisplay;
    configuration.allowsChangingSelectedContent = NO;
    picker.defaultConfiguration = configuration;
    picker.active = YES;
    __weak typeof(self) weak = self;
    JardAnnotationPicker *observer = JardAnnotationPicker.new;
    observer.select = ^(SCContentFilter *filter) {
        dispatch_async(dispatch_get_main_queue(), ^{
            JardAnnotation *owner = weak;
            if ([owner.identifier isEqual:identifier] && [owner.phase isEqual:@"picking"]) [owner beginRecording:filter];
        });
    };
    observer.finish = ^(NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            JardAnnotation *owner = weak;
            if (![owner.identifier isEqual:identifier] || ![owner.phase isEqual:@"picking"]) return;
            if (error) [owner fail:error.localizedDescription keepMovie:NO];
            else [owner cancel];
        });
    };
    self.pickerObserver = observer;
    [picker addObserver:observer];
    NSString *message = window ? @"Choose the window you want to annotate." : @"Choose the display you want to annotate.";
    if ([self.source isEqual:@"area"]) message = @"Choose the display that contains the area you want to annotate.";
    [self state:@"picking" message:message];
    [picker presentPickerUsingContentStyle:window ? SCShareableContentStyleWindow : SCShareableContentStyleDisplay];
    if (reply) reply([self status], nil);
}

- (void)endPicker {
    if (!self.pickerObserver) return;
    SCContentSharingPicker *picker = SCContentSharingPicker.sharedPicker;
    [picker removeObserver:self.pickerObserver];
    self.pickerObserver = nil;
    picker.active = NO;
}

- (void)selectRegionOnScreen:(NSScreen *)screen filter:(SCContentFilter *)filter {
    NSString *identifier = self.identifier;
    __weak typeof(self) weak = self;
    self.regionPanel = JardAnnotationRegionPanel(screen, ^(NSRect area) {
        JardAnnotation *owner = weak;
        if (![owner.identifier isEqual:identifier] || ![owner.phase isEqual:@"selecting"]) return;
        if (NSIsEmptyRect(area)) { [owner stop]; return; }
        owner.captureArea = area;
        [owner.regionPanel orderOut:nil];
        owner.regionPanel = nil;
        [owner beginRecording:filter];
    });
    [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(regionScreenChanged:)
        name:NSApplicationDidChangeScreenParametersNotification object:NSApp];
    [self state:@"selecting" message:@"Drag to select an area. Press Escape to cancel."];
    [self.regionPanel makeKeyAndOrderFront:nil];
}

- (void)regionScreenChanged:(NSNotification *)notification {
    if ([self.phase isEqual:@"selecting"]) {
        [self conclude:@"cancelled" message:@"The display layout changed. Choose an area again."];
    } else if ([self.phase isEqual:@"starting"] || [self.phase isEqual:@"recording"]) {
        [self stop];
    }
}

- (BOOL)prepareMovie:(NSString *)name {
    NSURL *directory = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:
        [@"jard-annotation-" stringByAppendingString:self.identifier]] isDirectory:YES];
    NSError *error = nil;
    if (![NSFileManager.defaultManager createDirectoryAtURL:directory withIntermediateDirectories:NO
        attributes:@{NSFilePosixPermissions: @0700} error:&error]) {
        [self fail:error.localizedDescription keepMovie:NO];
        return NO;
    }
    self.directory = directory;
    self.movie = [directory URLByAppendingPathComponent:name];
    return YES;
}

- (void)authorizePhone:(NSString *)identifier reply:(JardDictationReply)reply {
    void (^resumed)(BOOL) = ^(BOOL allowed) {
        if (![self.identifier isEqual:identifier]) return;
        if (allowed) [self beginPhoneRecording];
        else [self fail:@"Allow Jaro in System Settings › Privacy & Security › Camera, then try again." keepMovie:NO];
    };
    AVAuthorizationStatus permission = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    if (permission == AVAuthorizationStatusNotDetermined) {
        [self state:@"starting" message:@"Allow Camera access to mirror the wired screen."];
        if (reply) reply([self status], nil);
        [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo completionHandler:^(BOOL allowed) {
            dispatch_async(dispatch_get_main_queue(), ^{ resumed(allowed); });
        }];
        return;
    }
    resumed(permission == AVAuthorizationStatusAuthorized);
    if (reply) reply(self.busy ? [self status] : nil, self.busy ? nil : self.message);
}

- (void)beginPhoneRecording {
    if (![self prepareMovie:@"Wired capture.mov"]) return;
    [self state:@"starting" message:@"Opening the wired screen mirror…"];
    JardPhoneAnnotation *phone = [[JardPhoneAnnotation alloc] initWithMovie:self.movie microphone:self.microphone];
    self.phone = phone;
    NSString *identifier = self.identifier;
    __weak typeof(self) weak = self;
    phone.started = ^(NSTimeInterval uptime) {
        JardAnnotation *owner = weak;
        if (![owner.identifier isEqual:identifier] || ![owner.phase isEqual:@"starting"]) return;
        NSRect target = owner.phone.pictureOnScreen;
        NSScreen *screen = owner.phone.panel.screen ?: NSScreen.mainScreen;
        if (!screen || NSIsEmptyRect(target)) {
            [owner fail:@"The wired screen preview is unavailable." keepMovie:YES];
            return;
        }
        [owner preparePanels:screen target:target];
        owner.ink.startTime = uptime;
        [owner.phone.panel addChildWindow:owner.overlay ordered:NSWindowAbove];
        [owner.controls orderFrontRegardless];
        [owner state:@"recording" message:@"Recording the wired screen and your Mac microphone."];
    };
    phone.pictureChanged = ^{
        JardAnnotation *owner = weak;
        if ([owner.identifier isEqual:identifier] && [owner.phase isEqual:@"recording"]) [owner updatePhoneOverlay];
    };
    phone.stopRequested = ^{
        JardAnnotation *owner = weak;
        if ([owner.identifier isEqual:identifier]) [owner stop];
    };
    phone.finished = ^(BOOL usable, NSString *error) {
        JardAnnotation *owner = weak;
        if (![owner.identifier isEqual:identifier] || owner.closed) return;
        owner.phone = nil;
        if ([owner.phase isEqual:@"cancelling"]) {
            [owner conclude:@"cancelled" message:@"Annotation discarded."];
        } else if (usable) {
            [owner hideRecordingControls];
            [owner renderAnnotation:@"Rendering drawings over the wired screen recording…"];
        } else {
            [owner fail:error ?: @"No recording was saved from the wired screen." keepMovie:YES];
        }
    };
    [phone start];
}

- (void)updatePhoneOverlay {
    NSRect target = self.phone.pictureOnScreen;
    if (NSIsEmptyRect(target)) {
        [self.ink endStroke];
        [self.overlay orderOut:nil];
        return;
    }
    if (!NSEqualRects(self.overlay.frame, target)) {
        [self.ink endStroke];
        [self.overlay setFrame:target display:YES];
    }
    if (!self.overlay.visible) [self.overlay orderFrontRegardless];
    NSScreen *screen = self.phone.panel.screen;
    if (screen && screen != self.controls.screen)
        [self.controls setFrame:JardAnnotationControlsRect(screen.visibleFrame, NSZeroRect) display:YES];
}

- (void)beginRecording:(SCContentFilter *)filter {
    BOOL choosingWindow = [self.source isEqual:@"window"];
    BOOL choosingArea = [self.source isEqual:@"area"];
    SCWindow *window = choosingWindow && filter.style == SCShareableContentStyleWindow && filter.includedWindows.count == 1
        ? filter.includedWindows.firstObject : nil;
    SCDisplay *display = !choosingWindow && filter.style == SCShareableContentStyleDisplay && filter.includedDisplays.count == 1
        ? filter.includedDisplays.firstObject : nil;
    NSRect target = window ? JardAnnotationAppKitRect(window.frame, CGRectGetHeight(CGDisplayBounds(CGMainDisplayID()))) : NSZeroRect;
    NSScreen *screen = nil;
    CGFloat area = 0;
    for (NSScreen *item in NSScreen.screens) {
        if (display && [item.deviceDescription[@"NSScreenNumber"] unsignedIntValue] == display.displayID) screen = item;
        if (window) {
            NSRect visible = NSIntersectionRect(target, item.frame);
            CGFloat overlap = NSWidth(visible) * NSHeight(visible);
            if (overlap > area) { area = overlap; screen = item; }
        }
    }
    if (!screen) { [self fail:choosingWindow ? @"The selected window is unavailable." : @"The selected display is unavailable." keepMovie:NO]; return; }
    CGRect sourceRect = CGRectZero;
    if (choosingArea) {
        if (NSIsEmptyRect(self.captureArea)) { [self selectRegionOnScreen:screen filter:filter]; return; }
        target = NSIntersectionRect(self.captureArea, screen.frame);
        sourceRect = JardAnnotationDisplaySourceRect(target, screen.frame);
        if (CGRectIsEmpty(sourceRect)) {
            [self fail:@"The selected area is no longer on the chosen display." keepMovie:NO];
            return;
        }
    }
    self.windowID = window.windowID;
    self.ownerPID = window.owningApplication.processID;
    NSString *bundle = window.owningApplication.bundleIdentifier;
    NSDictionary *selectedApp = bundle.length ? @{@"app": bundle,
        @"name": window.owningApplication.applicationName ?: bundle} : nil;
    CGFloat heightOfPrimary = CGRectGetHeight(CGDisplayBounds(CGMainDisplayID()));
    CGRect captured = window ? window.frame : JardAnnotationAppKitRect(choosingArea ? target : screen.frame, heightOfPrimary);
    if (!window || selectedApp)
        self.routingOrigin = JardAnnotationRoutingOrigin(self.originSnapshot, selectedApp, self.ownerPID, window.title, captured);
    [self state:@"starting" message:@"Starting screen capture…"];
    if (![self prepareMovie:window ? @"Window capture.mp4" : @"Screen annotation.mp4"]) return;
    NSError *error = nil;
    SCStreamConfiguration *configuration = SCStreamConfiguration.new;
    if (choosingArea) configuration.sourceRect = sourceRect;
    CGRect sourceBounds = choosingArea ? sourceRect : filter.contentRect;
    double width = CGRectGetWidth(sourceBounds) * filter.pointPixelScale;
    double height = CGRectGetHeight(sourceBounds) * filter.pointPixelScale;
    double scale = fmin(1, 2560 / fmax(width, height));
    configuration.width = (size_t)fmax(2, floor(width * scale / 2) * 2);
    configuration.height = (size_t)fmax(2, floor(height * scale / 2) * 2);
    configuration.minimumFrameInterval = CMTimeMake(1, 30);
    configuration.queueDepth = 5;
    configuration.showsCursor = YES;
    configuration.captureMicrophone = YES;
    configuration.capturesAudio = NO;
    if (window) {
        configuration.ignoreShadowsSingleWindow = YES;
        configuration.preservesAspectRatio = NO;
        configuration.scalesToFit = YES;
    }
    SCRecordingOutputConfiguration *file = SCRecordingOutputConfiguration.new;
    file.outputURL = self.movie;
    file.outputFileType = AVFileTypeMPEG4;
    self.recording = [[SCRecordingOutput alloc] initWithConfiguration:file delegate:self];
    self.stream = [[SCStream alloc] initWithFilter:filter configuration:configuration delegate:self];
    if (![self.stream addRecordingOutput:self.recording error:&error]) {
        [self fail:error.localizedDescription keepMovie:NO];
        return;
    }
    [self preparePanels:screen target:window || choosingArea ? target : screen.frame];
    NSString *identifier = self.identifier;
    [self.stream startCaptureWithCompletionHandler:^(NSError *failure) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (![self.identifier isEqual:identifier]) return;
            if (failure) [self fail:failure.localizedDescription keepMovie:NO];
        });
    }];
}

- (void)preparePanels:(NSScreen *)screen target:(NSRect)target {
    NSWindowStyleMask style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel;
    JardAnnotationPanel *overlay = [[JardAnnotationPanel alloc] initWithContentRect:target styleMask:style
        backing:NSBackingStoreBuffered defer:NO];
    overlay.opaque = NO;
    overlay.backgroundColor = NSColor.clearColor;
    overlay.ignoresMouseEvents = YES;
    overlay.level = NSStatusWindowLevel - 1;
    overlay.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary;
    self.ink = [[JardInkView alloc] initWithFrame:overlay.contentView.bounds];
    self.ink.autoresizingMask = NSViewWidthSizable | NSViewHeightSizable;
    self.ink.renderedInk = self.windowID != 0 || self.phone != nil;
    overlay.contentView = self.ink;
    self.overlay = overlay;

    JardAnnotationPanel *controls = [[JardAnnotationPanel alloc] initWithContentRect:
        JardAnnotationControlsRect(screen.visibleFrame, self.captureArea) styleMask:style backing:NSBackingStoreBuffered defer:NO];
    controls.level = NSStatusWindowLevel;
    controls.opaque = NO;
    controls.backgroundColor = NSColor.clearColor;
    controls.collectionBehavior = overlay.collectionBehavior;
    NSVisualEffectView *surface = [[NSVisualEffectView alloc] initWithFrame:controls.contentView.bounds];
    surface.material = NSVisualEffectMaterialHUDWindow;
    surface.blendingMode = NSVisualEffectBlendingModeBehindWindow;
    surface.state = NSVisualEffectStateActive;
    surface.wantsLayer = YES;
    surface.layer.cornerRadius = 13;
    surface.layer.masksToBounds = YES;
    controls.contentView = surface;
    self.penButton = [NSButton buttonWithTitle:@"Draw" target:self action:@selector(togglePen:)];
    self.penButton.toolTip = @"Drag to draw. Hold Option–Shift–S to draw without clicking.";
    NSButton *clear = [NSButton buttonWithTitle:@"Clear Ink" target:self action:@selector(clearInk:)];
    NSButton *stop = [NSButton buttonWithTitle:@"Stop & Save" target:self action:@selector(stopButton:)];
    self.penButton.frame = NSMakeRect(10, 8, 74, 30);
    clear.frame = NSMakeRect(88, 8, 84, 30);
    stop.frame = NSMakeRect(174, 8, 108, 30);
    [surface addSubview:self.penButton];
    [surface addSubview:clear];
    [surface addSubview:stop];
    self.controls = controls;
}

- (void)togglePen:(id)sender {
    if (![self.phase isEqual:@"recording"]) return;
    self.penLatched = !self.penLatched;
    [self syncPen];
}

- (void)syncPen {
    BOOL drawing = self.penLatched || _keys.shiftPen || _keys.heldPen;
    if (!drawing || self.ink.followsPointer != _keys.heldPen) [self.ink endStroke];
    self.ink.followsPointer = _keys.heldPen;
    self.overlay.ignoresMouseEvents = !drawing;
    self.penButton.title = drawing ? @"Done" : @"Draw";
}

- (void)drawPointerAtScreenPoint:(NSPoint)point {
    if (!self.overlay.visible || !NSPointInRect(point, self.overlay.frame) ||
        (self.controls.visible && NSPointInRect(point, self.controls.frame))) {
        [self.ink endStroke];
        return;
    }
    NSPoint local = [self.ink convertPoint:[self.overlay convertPointFromScreen:point] fromView:nil];
    if (self.ink.strokeActive) [self.ink continueStrokeAt:local];
    else [self.ink beginStrokeAt:local];
}

- (void)armKeyboardPen {
    [self.holdTimer invalidate];
    __weak typeof(self) weak = self;
    self.holdTimer = [NSTimer timerWithTimeInterval:JardAnnotationHoldDelay repeats:NO block:^(NSTimer *timer) {
        JardAnnotation *owner = weak;
        if (!owner) return;
        owner.holdTimer = nil;
        if (![owner.phase isEqual:@"recording"]) return;
        JardAnnotationKey(&owner->_keys, JardAnnotationRecording, JardAnnotationHoldTimer,
            NSProcessInfo.processInfo.systemUptime, owner.ink.startTime, 0, false, owner.penLatched);
        [owner syncPen];
        if (owner->_keys.heldPen) [owner drawPointerAtScreenPoint:NSEvent.mouseLocation];
    }];
    [NSRunLoop.mainRunLoop addTimer:self.holdTimer forMode:NSRunLoopCommonModes];
}

- (BOOL)handleEvent:(CGEventType)type event:(CGEventRef)event shortcutCode:(CGKeyCode)shortcutCode
           allowStart:(BOOL)allowStart start:(dispatch_block_t)start {
    if (self.closed) return NO;
    if (!event || type == kCGEventTapDisabledByTimeout || type == kCGEventTapDisabledByUserInput) {
        self.keyboardGeneration++;
        _keys = (JardAnnotationKeys){0};
        [self.holdTimer invalidate];
        self.holdTimer = nil;
        self.penLatched = NO;
        [self syncPen];
        return NO;
    }
    if (type == kCGEventMouseMoved || type == kCGEventLeftMouseDragged) {
        if (_keys.heldPen && [self.phase isEqual:@"recording"]) {
            CGPoint location = CGEventGetLocation(event);
            CGFloat height = CGRectGetHeight(CGDisplayBounds(CGMainDisplayID()));
            [self drawPointerAtScreenPoint:NSMakePoint(location.x, height - location.y)];
        }
        return NO;
    }
    JardAnnotationInputMode mode = JardAnnotationUnavailable;
    if ([self.phase isEqual:@"recording"]) mode = JardAnnotationRecording;
    else if ([self.phase isEqual:@"picking"] || [self.phase isEqual:@"selecting"]) mode = JardAnnotationPicking;
    else if (!self.busy && allowStart) mode = JardAnnotationReady;
    BOOL down = type == kCGEventKeyDown;
    CGKeyCode code = (CGKeyCode)CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode);
    JardAnnotationKeyEvent kind;
    if (type == kCGEventFlagsChanged) kind = JardAnnotationModifiers;
    else if (type != kCGEventKeyUp && !down) return NO;
    else if (code == shortcutCode) kind = down ? JardAnnotationChordDown : JardAnnotationChordUp;
    else if (code == kVK_Escape) kind = down ? JardAnnotationEscapeDown : JardAnnotationEscapeUp;
    else if (down) kind = JardAnnotationOtherDown;
    else return NO;

    CGEventFlags flags = CGEventGetFlags(event);
    unsigned modifiers = (flags & kCGEventFlagMaskAlternate ? JardAnnotationOption : 0) |
        (flags & kCGEventFlagMaskShift ? JardAnnotationShift : 0) |
        (flags & (kCGEventFlagMaskCommand | kCGEventFlagMaskControl | kCGEventFlagMaskSecondaryFn) ? JardAnnotationOtherModifier : 0);
    BOOL wasDrawing = _keys.heldPen || _keys.shiftPen;
    unsigned action = JardAnnotationKey(&_keys, mode, kind, NSProcessInfo.processInfo.systemUptime,
        self.ink.startTime, modifiers, down && CGEventGetIntegerValueField(event, kCGKeyboardEventAutorepeat), self.penLatched);
    if (action & JardAnnotationArmDraw) [self armKeyboardPen];
    else if (_keys.press != JardAnnotationDrawPress && self.holdTimer) {
        [self.holdTimer invalidate];
        self.holdTimer = nil;
    }
    if (action & JardAnnotationEndDrawing) self.penLatched = NO;
    if (wasDrawing != (_keys.heldPen || _keys.shiftPen) || (action & JardAnnotationEndDrawing)) [self syncPen];
    NSUInteger generation = self.keyboardGeneration;
    if ((action & JardAnnotationBegin) && start) dispatch_async(dispatch_get_main_queue(), ^{
        if (!self.closed && self.keyboardGeneration == generation) start();
    });
    if (action & (JardAnnotationSave | JardAnnotationCancelPicker)) {
        NSString *identifier = self.identifier;
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.keyboardGeneration != generation || ![self.identifier isEqual:identifier]) return;
            if ((action & JardAnnotationSave) && [self.phase isEqual:@"recording"]) [self stop];
            if ((action & JardAnnotationCancelPicker) && ([self.phase isEqual:@"picking"] || [self.phase isEqual:@"selecting"])) [self stop];
        });
    }
    return (action & JardAnnotationTake) != 0;
}

- (void)clearInk:(id)sender {
    [self.ink clear];
}

- (void)stopButton:(id)sender { [self stop]; }

- (void)recordingOutputDidStartRecording:(SCRecordingOutput *)recordingOutput {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (recordingOutput != self.recording || ![self.phase isEqual:@"starting"]) return;
        self.ink.startTime = NSProcessInfo.processInfo.systemUptime;
        [self.controls orderFrontRegardless];
        NSString *message = self.windowID ? @"Recording the selected window and your microphone."
                                          : @"Recording the selected display and your microphone.";
        if ([self.source isEqual:@"area"]) message = @"Recording the selected area and your microphone.";
        [self state:@"recording" message:message];
        if (!self.windowID) [self.overlay orderFrontRegardless];
        else {
            [[NSRunningApplication runningApplicationWithProcessIdentifier:self.ownerPID] activateWithOptions:0];
            __weak typeof(self) weak = self;
            self.windowTracker = [NSTimer timerWithTimeInterval:.12 repeats:YES block:^(NSTimer *timer) { [weak updateWindowOverlay]; }];
            [NSRunLoop.mainRunLoop addTimer:self.windowTracker forMode:NSRunLoopCommonModes];
            [self updateWindowOverlay];
        }
    });
}

- (void)updateWindowOverlay {
    if (!self.windowID || ![self.phase isEqual:@"recording"]) return;
    NSArray *info = CFBridgingRelease(CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow, self.windowID));
    NSDictionary *window = info.firstObject;
    if (!window) { [self stop]; return; }
    NSDictionary *position = window[(__bridge NSString *)kCGWindowBounds];
    CGRect bounds;
    if (![window[(__bridge NSString *)kCGWindowIsOnscreen] boolValue]
        || NSWorkspace.sharedWorkspace.frontmostApplication.processIdentifier != self.ownerPID
        || !position || !CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)position, &bounds)) {
        [self.overlay orderOut:nil];
        return;
    }
    NSRect target = JardAnnotationAppKitRect(bounds, CGRectGetHeight(CGDisplayBounds(CGMainDisplayID())));
    if (!NSEqualRects(self.overlay.frame, target)) [self.overlay setFrame:target display:YES];
    if (!self.overlay.visible) [self.overlay orderFrontRegardless];
    NSScreen *screen = self.overlay.screen;
    if (screen && screen != self.controls.screen) {
        NSRect available = screen.visibleFrame;
        [self.controls setFrameOrigin:NSMakePoint(NSMidX(available) - NSWidth(self.controls.frame) / 2, NSMinY(available) + 24)];
    }
}

- (void)hideRecordingControls {
    self.stoppedAfter = self.elapsed;
    [self.windowTracker invalidate];
    self.windowTracker = nil;
    [self.controls orderOut:nil];
    [self.overlay orderOut:nil];
}

- (BOOL)stop {
    if ([self.phase isEqual:@"picking"] || [self.phase isEqual:@"selecting"] ||
        ([self.phase isEqual:@"starting"] && !self.stream)) {
        return [self cancel];
    }
    if (![self.phase isEqual:@"recording"] && ![self.phase isEqual:@"starting"]) return NO;
    [self hideRecordingControls];
    [self state:@"saving" message:@"Saving the screen recording and transcribing narration…"];
    if (self.phone) {
        [self.phone stopDiscarding:NO];
        return YES;
    }
    SCStream *stream = self.stream;
    [stream stopCaptureWithCompletionHandler:^(NSError *error) {
        if (error) dispatch_async(dispatch_get_main_queue(), ^{
            if (stream == self.stream && self.recording) [self fail:error.localizedDescription keepMovie:YES];
        });
    }];
    return YES;
}

- (BOOL)cancel {
    if (!self.busy || [self.phase isEqual:@"saving"] || [self.phase isEqual:@"cancelling"]) return NO;
    if (self.acquiringLease) {
        [self state:@"cancelling" message:@"Discarding the annotation…"];
        return YES;
    }
    if (self.phone) {
        [self hideRecordingControls];
        [self state:@"cancelling" message:@"Discarding the annotation…"];
        [self.phone stopDiscarding:YES];
        return YES;
    }
    SCStream *stream = self.stream;
    if (!stream) {
        [self conclude:@"cancelled" message:@"Annotation discarded."];
        return YES;
    }
    [self hideRecordingControls];
    [self state:@"cancelling" message:@"Discarding the annotation…"];
    [stream stopCaptureWithCompletionHandler:^(NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.stream != stream || ![self.phase isEqual:@"cancelling"]) return;
            if (error) { [self fail:error.localizedDescription keepMovie:NO]; return; }
            self.stream = nil;
            [self conclude:@"cancelled" message:@"Annotation discarded."];
        });
    }];
    return YES;
}

- (void)recordingOutputDidFinishRecording:(SCRecordingOutput *)recordingOutput {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (recordingOutput != self.recording || !self.busy || self.closed) return;
        if ([self.phase isEqual:@"cancelling"]) return;
        self.recording = nil;
        [self hideRecordingControls];
        if (!self.windowID) { [self importMovie]; return; }
        NSURL *output = [self.directory URLByAppendingPathComponent:@"Screen annotation.mp4"];
        if (!self.ink.events.count) {
            NSError *error = nil;
            if (![NSFileManager.defaultManager moveItemAtURL:self.movie toURL:output error:&error]) {
                [self fail:error.localizedDescription keepMovie:YES]; return;
            }
            self.movie = output;
            [self importMovie];
            return;
        }
        [self renderAnnotation:@"Rendering drawings over the window recording…"];
    });
}

- (void)renderAnnotation:(NSString *)message {
    NSURL *output = [self.directory URLByAppendingPathComponent:@"Screen annotation.mp4"];
    NSString *identifier = self.identifier;
    [self state:@"saving" message:message];
    JardRenderAnnotation(self.movie, output, self.ink.events.copy, ^(NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (![self.identifier isEqual:identifier] || self.closed) return;
            if (error) { [self fail:@"Could not render the annotation. The original recording is available in Finder." keepMovie:YES]; return; }
            self.movie = output;
            [self importMovie];
        });
    });
}

- (void)importMovie {
    [self state:@"saving" message:@"Preparing the annotation and contact sheet…"];
    NSString *identifier = self.identifier;
    JardWriteAnnotationAssets(self.movie, self.source, self.routingOrigin, self.ink.events.copy, ^(NSError *error) {
        if (![self.identifier isEqual:identifier] || self.closed) return;
        if (error) { [self fail:@"Could not save the drawings. The recording is available in Finder." keepMovie:YES]; return; }
        [self submitAnnotation];
    });
}

- (void)submitAnnotation {
    [self state:@"saving" message:@"Transcribing narration and saving the annotation…"];
    NSString *identifier = self.identifier;
    self.send(@"jard.importAnnotation", @{@"id": identifier, @"path": self.movie.path,
        @"languages": self.languages ?: @[], @"assets": @YES, @"routeOrigin": self.routingOrigin ?: @{}}, ^(id result, NSString *error) {
        if (![self.identifier isEqual:identifier]) return;
        if (error) { [self fail:error keepMovie:YES]; return; }
        self.sessionID = result[@"id"];
        [self conclude:@"finished" message:@"Annotation saved to Sessions."];
    });
}

- (void)recordingOutput:(SCRecordingOutput *)recordingOutput didFailWithError:(NSError *)error {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (recordingOutput == self.recording && ![self.phase isEqual:@"cancelling"])
            [self fail:error.localizedDescription keepMovie:YES];
    });
}

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (stream != self.stream || !self.recording) return;
        if ([self.phase isEqual:@"cancelling"]) {
            self.stream = nil;
            [self conclude:@"cancelled" message:@"Annotation discarded."];
        } else [self fail:error.localizedDescription keepMovie:YES];
    });
}

- (void)fail:(NSString *)message keepMovie:(BOOL)keep {
    NSNumber *size = nil;
    if (keep && [self.movie getResourceValue:&size forKey:NSURLFileSizeKey error:nil] && size.longLongValue > 0) {
        self.recovery = self.movie;
        self.directory = nil;
    }
    [self conclude:@"error" message:message ?: @"The screen annotation could not be saved."];
}

- (void)reset {
    self.keyboardGeneration++;
    // Keep key-up ownership if the picker or recorder stopped while its shortcut was down.
    _keys.press = JardAnnotationNoPress;
    _keys.shiftPen = _keys.heldPen = NO;
    self.penLatched = NO;
    [self.holdTimer invalidate];
    self.holdTimer = nil;
    [self endPicker];
    [NSNotificationCenter.defaultCenter removeObserver:self name:NSApplicationDidChangeScreenParametersNotification object:NSApp];
    [self.regionPanel orderOut:nil];
    self.regionPanel = nil;
    self.captureArea = NSZeroRect;
    [self.windowTracker invalidate];
    self.windowTracker = nil;
    self.windowID = 0;
    if (self.overlay) [self.phone.panel removeChildWindow:self.overlay];
    [self.phone stopDiscarding:YES];
    self.phone = nil;
    self.microphone = nil;
    self.originSnapshot = self.routingOrigin = nil;
    [self.overlay orderOut:nil];
    [self.controls orderOut:nil];
    self.ink = nil;
    self.stoppedAfter = nil;
    self.overlay = self.controls = nil;
    SCStream *stream = self.stream;
    self.stream = nil;
    self.recording = nil;
    [stream stopCaptureWithCompletionHandler:nil];
    if (self.leased) self.send(@"jard.annotationLease", @{@"id": self.identifier, @"active": @NO}, nil);
    self.leased = NO;
    self.acquiringLease = NO;
    self.identifier = nil;
    if (self.directory) [NSFileManager.defaultManager removeItemAtURL:self.directory error:nil];
    self.directory = self.movie = nil;
}

- (void)reveal {
    if (self.recovery) [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[self.recovery]];
}

- (void)shutdown {
    self.closed = YES;
    if ([self.phase isEqual:@"cancelling"]) [self conclude:@"cancelled" message:@"Annotation discarded."];
    else if (self.busy) [self fail:@"Annotation was interrupted." keepMovie:YES];
}
@end
