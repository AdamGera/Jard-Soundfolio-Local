#import "jard_annotation_region.h"

static const CGFloat JardMinimumRegion = 32;

NSRect JardAnnotationDragRect(NSPoint start, NSPoint end, NSRect bounds) {
    NSRect drag = NSMakeRect(MIN(start.x, end.x), MIN(start.y, end.y), fabs(end.x - start.x), fabs(end.y - start.y));
    return NSIntersectionRect(NSIntegralRect(drag), bounds);
}

CGRect JardAnnotationDisplaySourceRect(NSRect area, NSRect display) {
    NSRect clipped = NSIntersectionRect(area, display);
    if (NSIsEmptyRect(clipped)) return CGRectZero;
    return CGRectMake(NSMinX(clipped) - NSMinX(display), NSMaxY(display) - NSMaxY(clipped),
                      NSWidth(clipped), NSHeight(clipped));
}

NSRect JardAnnotationControlsRect(NSRect available, NSRect area) {
    const CGFloat width = 292, height = 46;
    const CGFloat horizontalInset = MAX(0, MIN(24, (NSWidth(available) - width) / 2));
    const CGFloat verticalInset = MAX(0, MIN(24, (NSHeight(available) - height) / 2));
    const CGFloat x[] = {NSMidX(available) - width / 2, NSMinX(available) + horizontalInset,
                         NSMaxX(available) - width - horizontalInset};
    const CGFloat y[] = {NSMinY(available) + verticalInset, NSMaxY(available) - height - verticalInset};
    NSRect best = NSMakeRect(x[0], y[0], width, height);
    CGFloat leastOverlap = width * height;
    for (NSUInteger row = 0; row < 2; row++) {
        for (NSUInteger column = 0; column < 3; column++) {
            NSRect candidate = NSMakeRect(x[column], y[row], width, height);
            NSRect clipped = NSIntersectionRect(candidate, area);
            CGFloat overlap = NSWidth(clipped) * NSHeight(clipped);
            if (overlap == 0) return candidate;
            if (overlap < leastOverlap) { best = candidate; leastOverlap = overlap; }
        }
    }
    return best;
}

@interface JardRegionView : NSView
@property(nonatomic, copy) void (^complete)(NSRect);
@property(nonatomic) NSPoint anchor;
@property(nonatomic) NSRect selection;
@end

@implementation JardRegionView
- (BOOL)acceptsFirstResponder { return YES; }
- (BOOL)acceptsFirstMouse:(NSEvent *)event { return YES; }
- (void)resetCursorRects { [self addCursorRect:self.bounds cursor:NSCursor.crosshairCursor]; }

- (void)mouseDown:(NSEvent *)event {
    self.anchor = [self convertPoint:event.locationInWindow fromView:nil];
    self.selection = NSZeroRect;
    self.needsDisplay = YES;
}

- (void)mouseDragged:(NSEvent *)event {
    self.selection = JardAnnotationDragRect(self.anchor, [self convertPoint:event.locationInWindow fromView:nil], self.bounds);
    self.needsDisplay = YES;
}

- (void)mouseUp:(NSEvent *)event {
    [self mouseDragged:event];
    if (NSWidth(self.selection) < JardMinimumRegion || NSHeight(self.selection) < JardMinimumRegion) {
        self.selection = NSZeroRect;
        self.needsDisplay = YES;
        return;
    }
    NSRect windowRect = [self convertRect:self.selection toView:nil];
    if (self.complete) self.complete([self.window convertRectToScreen:windowRect]);
}

- (void)cancelOperation:(id)sender {
    if (self.complete) self.complete(NSZeroRect);
}

- (void)keyDown:(NSEvent *)event {
    if (event.keyCode == 53) [self cancelOperation:nil];
    else [super keyDown:event];
}

- (void)drawRect:(NSRect)dirtyRect {
    [[NSColor colorWithWhite:0 alpha:.48] setFill];
    NSRectFill(dirtyRect);
    if (NSIsEmptyRect(self.selection)) return;
    NSRectFillUsingOperation(self.selection, NSCompositingOperationClear);
    [[NSColor colorWithSRGBRed:1 green:.55 blue:.34 alpha:1] setStroke];
    [NSBezierPath strokeRect:NSInsetRect(self.selection, .5, .5)];
}
@end

@interface JardRegionPanel : NSPanel
@end

@implementation JardRegionPanel
- (BOOL)canBecomeKeyWindow { return YES; }
- (BOOL)canBecomeMainWindow { return NO; }
@end

NSPanel *JardAnnotationRegionPanel(NSScreen *screen, void (^complete)(NSRect)) {
    JardRegionPanel *panel = [[JardRegionPanel alloc] initWithContentRect:screen.frame
        styleMask:NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        backing:NSBackingStoreBuffered defer:NO];
    panel.opaque = NO;
    panel.backgroundColor = NSColor.clearColor;
    panel.hasShadow = NO;
    panel.hidesOnDeactivate = NO;
    panel.level = NSStatusWindowLevel;
    panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces | NSWindowCollectionBehaviorFullScreenAuxiliary;
    [panel setAccessibilityLabel:@"Select an area to annotate"];
    JardRegionView *view = [[JardRegionView alloc] initWithFrame:panel.contentView.bounds];
    view.complete = complete;
    panel.contentView = view;
    NSTextField *instruction = [NSTextField labelWithString:@"Drag to select the area to record"];
    instruction.alignment = NSTextAlignmentCenter;
    instruction.textColor = NSColor.whiteColor;
    instruction.font = [NSFont systemFontOfSize:15 weight:NSFontWeightSemibold];
    instruction.frame = NSMakeRect(100, NSHeight(view.bounds) - 83, MAX(0, NSWidth(view.bounds) - 200), 26);
    [view addSubview:instruction];
    NSButton *cancel = [NSButton buttonWithTitle:@"Cancel" target:view action:@selector(cancelOperation:)];
    cancel.frame = NSMakeRect(NSWidth(view.bounds) - 106, NSHeight(view.bounds) - 83, 85, 26);
    [view addSubview:cancel];
    [panel makeFirstResponder:view];
    return panel;
}
