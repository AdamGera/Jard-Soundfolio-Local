#import "jard_annotation_route_panel.h"

@interface JardRouteWindow : NSPanel
@end

@implementation JardRouteWindow
- (BOOL)canBecomeKeyWindow { return NO; }
- (BOOL)canBecomeMainWindow { return NO; }
@end

@interface JardAnnotationRoutePanel ()
@property(nonatomic, copy) BOOL (^available)(void);
@property(nonatomic, copy) void (^action)(NSString *, NSString *, BOOL);
@property(nonatomic, strong) JardRouteWindow *window;
@property(nonatomic, strong) NSTextField *title, *message;
@property(nonatomic, strong) NSButton *cancelButton;
@property(nonatomic, strong) NSTimer *timer;
@property(nonatomic, copy) NSString *capture, *phase;
@property(nonatomic) BOOL hovered;
@property(nonatomic) NSTimeInterval deadline;
@end

@implementation JardAnnotationRoutePanel

- (instancetype)initWithAvailability:(BOOL (^)(void))available
    action:(void (^)(NSString *, NSString *, BOOL))action {
    if (!(self = [super init])) return nil;
    _available = [available copy];
    _action = [action copy];
    return self;
}

- (BOOL)show:(NSString *)capture title:(NSString *)title {
    if (!self.available()) return NO;
    NSPoint mouse = NSEvent.mouseLocation;
    NSScreen *screen = NSScreen.mainScreen;
    for (NSScreen *candidate in NSScreen.screens) {
        if (NSPointInRect(mouse, candidate.frame)) { screen = candidate; break; }
    }
    if (!screen) return NO;
    [self dismiss];
    NSRect visible = screen.visibleFrame;
    CGFloat width = MIN(380, NSWidth(visible) - 24);
    if (width < 230) return NO;
    NSRect frame = NSMakeRect(NSMidX(visible) - width / 2, NSMaxY(visible) - 84, width, 72);
    self.window = [[JardRouteWindow alloc] initWithContentRect:frame
        styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel backing:NSBackingStoreBuffered defer:NO];
    self.window.releasedWhenClosed = NO;
    self.window.hidesOnDeactivate = NO;
    self.window.opaque = NO;
    self.window.backgroundColor = NSColor.clearColor;
    self.window.level = NSStatusWindowLevel;
    self.window.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary;

    NSVisualEffectView *surface = [[NSVisualEffectView alloc] initWithFrame:NSMakeRect(0, 0, width, 72)];
    surface.material = NSVisualEffectMaterialHUDWindow;
    surface.blendingMode = NSVisualEffectBlendingModeBehindWindow;
    surface.state = NSVisualEffectStateActive;
    surface.wantsLayer = YES;
    surface.layer.cornerRadius = 13;
    surface.layer.masksToBounds = YES;
    self.window.contentView = surface;
    self.title = [NSTextField labelWithString:[@"Send to " stringByAppendingString:title]];
    self.title.font = [NSFont systemFontOfSize:13 weight:NSFontWeightSemibold];
    self.title.lineBreakMode = NSLineBreakByTruncatingTail;
    self.title.frame = NSMakeRect(16, 42, width - 108, 17);
    self.message = [NSTextField labelWithString:@"Preparing automatic send…"];
    self.message.textColor = NSColor.secondaryLabelColor;
    self.message.font = [NSFont systemFontOfSize:12];
    self.message.lineBreakMode = NSLineBreakByTruncatingTail;
    self.message.frame = NSMakeRect(16, 20, width - 108, 17);
    self.cancelButton = [NSButton buttonWithTitle:@"Cancel" target:self action:@selector(cancel:)];
    self.cancelButton.frame = NSMakeRect(width - 86, 22, 72, 28);
    [surface addSubview:self.title];
    [surface addSubview:self.message];
    [surface addSubview:self.cancelButton];

    self.capture = capture;
    self.phase = @"offer";
    self.hovered = NSPointInRect(mouse, frame);
    [self.window orderFrontRegardless];
    if (!self.window.visible || !self.available()) { [self dismiss]; return NO; }
    __weak typeof(self) weak = self;
    self.timer = [NSTimer timerWithTimeInterval:0.10 repeats:YES block:^(NSTimer *timer) { [weak tick]; }];
    [NSRunLoop.mainRunLoop addTimer:self.timer forMode:NSRunLoopCommonModes];
    self.action(capture, @"shown", self.hovered);
    return YES;
}

- (void)update:(NSDictionary *)detail {
    NSString *capture = detail[@"capture"], *phase = detail[@"phase"];
    if (![capture isKindOfClass:NSString.class] || ![phase isKindOfClass:NSString.class] ||
        ![[NSUUID alloc] initWithUUIDString:capture]) return;
    if ([phase isEqual:@"offer"]) {
        NSString *title = [detail[@"title"] isKindOfClass:NSString.class] ? detail[@"title"] : @"OpenCode";
        if (![self show:capture title:title]) self.action(capture, @"cancel", NO);
        return;
    }
    if (![self.capture isEqual:capture]) return;
    self.phase = phase;
    if ([phase isEqual:@"cancelled"] || [phase isEqual:@"waiting"]) { [self dismiss]; return; }
    self.deadline = NSProcessInfo.processInfo.systemUptime + [detail[@"seconds"] doubleValue];
    self.cancelButton.hidden = [@[@"sending", @"sent", @"failed", @"unconfirmed"] containsObject:phase];
    NSString *message = [detail[@"message"] isKindOfClass:NSString.class] ? detail[@"message"] : @"";
    self.message.stringValue = [phase isEqual:@"held"] ? @"Paused while the pointer is here" : message;
    self.message.toolTip = self.message.stringValue;
    if ([@[@"sent", @"failed", @"unconfirmed"] containsObject:phase]) {
        [self.timer invalidate];
        self.timer = nil;
        __weak typeof(self) weak = self;
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, 3 * NSEC_PER_SEC), dispatch_get_main_queue(), ^{
            JardAnnotationRoutePanel *owner = weak;
            if ([owner.capture isEqual:capture]) [owner dismiss];
        });
    } else {
        [self tick];
    }
}

- (void)tick {
    if (!self.window.visible || !self.available()) { [self cancel:nil]; return; }
    BOOL hovered = NSPointInRect(NSEvent.mouseLocation, self.window.frame);
    if (hovered != self.hovered) {
        self.hovered = hovered;
        if ([@[@"offer", @"held", @"countdown"] containsObject:self.phase])
            self.action(self.capture, hovered ? @"hold" : @"resume", NO);
    }
    if ([self.phase isEqual:@"countdown"]) {
        NSInteger seconds = MAX(1, (NSInteger)ceil(self.deadline - NSProcessInfo.processInfo.systemUptime));
        self.message.stringValue = [NSString stringWithFormat:@"Sending in %ld…", (long)seconds];
    }
}

- (void)cancel:(id)sender {
    if (self.capture) self.action(self.capture, @"cancel", NO);
    [self dismiss];
}

- (void)dismiss {
    [self.timer invalidate];
    self.timer = nil;
    [self.window orderOut:nil];
    self.window = nil;
    self.title = self.message = nil;
    self.cancelButton = nil;
    self.capture = self.phase = nil;
}

@end
