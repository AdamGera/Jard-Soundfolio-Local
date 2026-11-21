#import "jard_meeting_panel.h"
#import <math.h>

BOOL JardValidMeetingCorner(NSString *corner) {
    return [@[@"topLeft", @"topRight", @"bottomLeft", @"bottomRight"] containsObject:corner ?: @""];
}

NSString *JardNearestMeetingCorner(NSRect visible, NSPoint center) {
    BOOL left = center.x < NSMidX(visible), top = center.y >= NSMidY(visible);
    return top ? (left ? @"topLeft" : @"topRight") : (left ? @"bottomLeft" : @"bottomRight");
}

NSRect JardMeetingCornerFrame(NSRect visible, NSSize size, NSString *corner) {
    BOOL left = [corner hasSuffix:@"Left"], top = [corner hasPrefix:@"top"];
    CGFloat x = left ? NSMinX(visible) + 18 : NSMaxX(visible) - size.width - 18;
    CGFloat y = top ? NSMaxY(visible) - size.height - 14 : NSMinY(visible) + 14;
    return NSMakeRect(MAX(NSMinX(visible), x), MAX(NSMinY(visible), y), size.width, size.height);
}

static NSScreen *screenAt(NSPoint point, NSScreen *fallback) {
    for (NSScreen *screen in NSScreen.screens) {
        if (NSPointInRect(point, screen.frame)) return screen;
    }
    return fallback ?: NSScreen.mainScreen;
}

@interface JardMeetingPanel ()
@property(nonatomic, getter=isDragging) BOOL dragging;
@property(nonatomic) NSRect originalFrame;
@property(nonatomic) NSPoint offset, recent, previous;
@property(nonatomic) NSTimeInterval recentTime, previousTime;
@property(nonatomic, strong) id escapeMonitor;
@end

@implementation JardMeetingPanel
- (BOOL)canBecomeKeyWindow { return NO; }
- (BOOL)canBecomeMainWindow { return NO; }
- (BOOL)reduceMotion { return NSWorkspace.sharedWorkspace.accessibilityDisplayShouldReduceMotion; }
- (NSTimeInterval)animationResizeTime:(NSRect)frame { return 0.26; }

- (void)moveToCorner:(NSString *)corner screen:(NSScreen *)screen animated:(BOOL)animated {
    NSScreen *display = screen ?: screenAt(NSEvent.mouseLocation, self.screen);
    if (!display) return;
    NSRect frame = JardMeetingCornerFrame(display.visibleFrame, self.frame.size, corner);
    [self setFrame:frame display:YES animate:animated && !self.reduceMotion];
}

- (void)beginDragAt:(NSPoint)point time:(NSTimeInterval)time {
    if (self.dragging) return;
    self.dragging = YES;
    self.originalFrame = self.frame;
    self.offset = NSMakePoint(point.x - NSMinX(self.frame), point.y - NSMinY(self.frame));
    self.recent = self.previous = point;
    self.recentTime = self.previousTime = time;
    __weak typeof(self) weak = self;
    self.escapeMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskKeyDown handler:^NSEvent *(NSEvent *event) {
        if (event.keyCode != 53 || !weak.isDragging) return event;
        [weak cancelDrag];
        return nil;
    }];
}

- (void)dragTo:(NSPoint)point time:(NSTimeInterval)time {
    if (!self.dragging) return;
    if (time - self.previousTime > 0.08) {
        self.previous = self.recent;
        self.previousTime = self.recentTime;
    }
    self.recent = point;
    self.recentTime = time;
    [self setFrameOrigin:NSMakePoint(point.x - self.offset.x, point.y - self.offset.y)];
}

- (void)endDragAt:(NSPoint)point time:(NSTimeInterval)time {
    if (!self.dragging) return;
    self.dragging = NO;
    if (self.escapeMonitor) [NSEvent removeMonitor:self.escapeMonitor];
    self.escapeMonitor = nil;
    if (NSEqualRects(self.frame, self.originalFrame)) return;
    NSScreen *display = screenAt(point, self.screen);
    if (!display) { [self setFrame:self.originalFrame display:YES]; return; }
    NSPoint center = NSMakePoint(NSMidX(self.frame), NSMidY(self.frame));
    NSTimeInterval duration = time - self.previousTime;
    if (!self.reduceMotion && time - self.recentTime < 0.08 && duration > 0.01 && duration < 0.18) {
        CGFloat dx = (point.x - self.previous.x) / duration, dy = (point.y - self.previous.y) / duration;
        CGFloat speed = hypot(dx, dy);
        CGFloat coast = 0.13 * (speed > 6000 ? 6000 / speed : 1);
        center = NSMakePoint(center.x + dx * coast, center.y + dy * coast);
    }
    NSString *corner = JardNearestMeetingCorner(display.visibleFrame, center);
    [self moveToCorner:corner screen:display animated:YES];
    if (self.placementChanged) self.placementChanged(corner, display);
}

- (void)cancelDrag {
    if (!self.dragging) return;
    self.dragging = NO;
    if (self.escapeMonitor) [NSEvent removeMonitor:self.escapeMonitor];
    self.escapeMonitor = nil;
    [self setFrame:self.originalFrame display:YES animate:!self.reduceMotion];
}

- (void)orderOut:(id)sender {
    [self cancelDrag];
    [super orderOut:sender];
}
@end

@implementation JardMeetingSurface
- (BOOL)acceptsFirstMouse:(NSEvent *)event { return YES; }
- (void)mouseDown:(NSEvent *)event { [(JardMeetingPanel *)self.window beginDragAt:NSEvent.mouseLocation time:event.timestamp]; }
- (void)mouseDragged:(NSEvent *)event { [(JardMeetingPanel *)self.window dragTo:NSEvent.mouseLocation time:event.timestamp]; }
- (void)mouseUp:(NSEvent *)event { [(JardMeetingPanel *)self.window endDragAt:NSEvent.mouseLocation time:event.timestamp]; }
@end

@implementation JardMeetingCaption
- (NSView *)hitTest:(NSPoint)point { return nil; }
@end
