#import <AppKit/AppKit.h>
#include <math.h>

static inline NSPoint JardDictationHUDOrigin(NSRect screen, NSRect visible, CGFloat safeTop,
                                             NSSize panel, BOOL bottom) {
    const CGFloat plateHeight = 26;
    CGFloat centerY = bottom ? NSMinY(visible) + 24 + plateHeight / 2 :
        fmin(NSMaxY(visible), NSMaxY(screen) - safeTop) - 6 - plateHeight / 2;
    return NSMakePoint(NSMidX(screen) - panel.width / 2, centerY - panel.height / 2);
}
