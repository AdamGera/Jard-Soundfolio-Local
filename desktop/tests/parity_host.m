// Compile with -DJARD_REFERENCE for the reference window, or link as
// JARD_TEST_SOURCE into a QA-only copy of the SDK host for screenshots.
#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

static WKWebView *findWebView(NSView *view) {
    if ([view isKindOfClass:WKWebView.class]) return (WKWebView *)view;
    for (NSView *child in view.subviews) {
        WKWebView *found = findWebView(child);
        if (found) return found;
    }
    return nil;
}

@interface ParityCapture : NSObject
@end
@implementation ParityCapture
+ (void)load {
    dispatch_async(dispatch_get_main_queue(), ^{
        NSDictionary *env = NSProcessInfo.processInfo.environment;
        NSString *directory = env[@"JARD_QA_PNG_DIR"];
        if (!directory.length) return;
        NSApp.appearance = [NSAppearance appearanceNamed:[env[@"JARD_QA_SCHEME"] isEqualToString:@"dark"]
            ? NSAppearanceNameDarkAqua : NSAppearanceNameAqua];
        __block BOOL busy = NO, sized = NO;
        __block NSString *previous = nil;
        NSMutableSet *captured = [NSMutableSet set];
        [NSTimer scheduledTimerWithTimeInterval:.12 repeats:YES block:^(NSTimer *timer) {
            if (busy) return;
            WKWebView *view = nil;
            for (NSWindow *window in NSApp.windows) {
                view = findWebView(window.contentView);
                if (view) break;
            }
            if (!view) return;
            if (!sized) {
                sized = YES;
                [view.window setContentSize:NSMakeSize([env[@"JARD_QA_WIDTH"] doubleValue],
                                                      [env[@"JARD_QA_HEIGHT"] doubleValue])];
                // Keep the user's real pointer from hovering a different row in each QA window.
                [view.window setFrameOrigin:NSMakePoint(NSMinX(NSScreen.mainScreen.frame) -
                    NSWidth(view.window.frame) - 200, NSMinY(NSScreen.mainScreen.frame))];
            }
            busy = YES;
            if (env[@"JARD_QA_PLAYBACK"]) {
                [view evaluateJavaScript:@"window.qaPoint?.splice(0,2)" completionHandler:^(id coordinates, NSError *error) {
                    busy = NO;
                    if (![coordinates isKindOfClass:NSArray.class] || [coordinates count]!=2) return;
                    CGFloat y=[coordinates[1] doubleValue];
                    NSPoint local=NSMakePoint([coordinates[0] doubleValue],view.isFlipped ? y : NSHeight(view.bounds)-y);
                    NSPoint point=[view convertPoint:local toView:nil];
                    NSView *hit=[view.window.contentView hitTest:[view.window.contentView.superview convertPoint:point fromView:nil]];
                    // Events target this test window. The system mouse and current application stay in place.
                    for (NSNumber *kind in @[@(NSEventTypeLeftMouseDown), @(NSEventTypeLeftMouseUp)]) {
                        NSEvent *event=[NSEvent mouseEventWithType:kind.unsignedIntegerValue location:point modifierFlags:0
                            timestamp:NSProcessInfo.processInfo.systemUptime windowNumber:view.window.windowNumber context:nil
                            eventNumber:0 clickCount:1 pressure:1];
                        if (event.type==NSEventTypeLeftMouseDown) [hit mouseDown:event];
                        else [hit mouseUp:event];
                    }
                }];
                return;
            }
            [view evaluateJavaScript:@"[location.href, !!window.zero, document.title, document.readyState, document.querySelector('main h1')?.textContent || '', document.getElementById('detail')?.textContent || ''].join(' | ')"
                completionHandler:^(id info, NSError *error) {
                    if (info && ![info isEqual:previous]) {
                        previous = [info copy];
                        fprintf(stderr, "%s\n", [info UTF8String]);
                    }
                }];
            [view evaluateJavaScript:@"document.documentElement.dataset.parityFrame || ''" completionHandler:^(id name, NSError *err) {
                if (err || ![name isKindOfClass:NSString.class] || ![name length] || [captured containsObject:name] ||
                    [name rangeOfCharacterFromSet:NSCharacterSet.alphanumericCharacterSet.invertedSet].location != NSNotFound) {
                    busy = NO;
                    return;
                }
                [captured addObject:name];
                [view takeSnapshotWithConfiguration:nil completionHandler:^(NSImage *img, NSError *error) {
                    if (img) {
                        // Normalize the Retina raster into CSS-pixel coordinates.
                        NSSize size = view.bounds.size;
                        NSBitmapImageRep *png = [[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL
                            pixelsWide:(NSInteger)size.width pixelsHigh:(NSInteger)size.height bitsPerSample:8 samplesPerPixel:4
                            hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace bytesPerRow:0 bitsPerPixel:0];
                        [NSGraphicsContext saveGraphicsState];
                        [NSGraphicsContext setCurrentContext:[NSGraphicsContext graphicsContextWithBitmapImageRep:png]];
                        [img drawInRect:NSMakeRect(0,0,size.width,size.height) fromRect:NSZeroRect
                              operation:NSCompositingOperationCopy fraction:1.0];
                        [NSGraphicsContext restoreGraphicsState];
                        NSString *file = [directory stringByAppendingPathComponent:[name stringByAppendingPathExtension:@"png"]];
                        [[png representationUsingType:NSBitmapImageFileTypePNG properties:@{}] writeToFile:file atomically:YES];
                    }
                    [view evaluateJavaScript:@"document.documentElement.dataset.parityCaptured = document.documentElement.dataset.parityFrame"
                        completionHandler:nil];
                    busy = NO;
                }];
            }];
        }];
    });
}
@end

#ifdef JARD_REFERENCE
int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSDictionary *env = NSProcessInfo.processInfo.environment;
        NSRect rect = NSMakeRect(0,0,[env[@"JARD_QA_WIDTH"] doubleValue],[env[@"JARD_QA_HEIGHT"] doubleValue]);
        NSWindow *window = [[NSWindow alloc] initWithContentRect:rect styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskResizable
            backing:NSBackingStoreBuffered defer:NO];
        WKWebViewConfiguration *config = [WKWebViewConfiguration new];
        config.websiteDataStore = WKWebsiteDataStore.nonPersistentDataStore;
        WKWebView *view = [[WKWebView alloc] initWithFrame:rect configuration:config];
        window.contentView = view;
        [window orderFront:nil];
        [view loadRequest:[NSURLRequest requestWithURL:[NSURL URLWithString:env[@"JARD_QA_URL"]]]];
        [app run];
    }
}
#endif
