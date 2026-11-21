#import <AppKit/AppKit.h>

NSRect JardAnnotationDragRect(NSPoint start, NSPoint end, NSRect bounds);
CGRect JardAnnotationDisplaySourceRect(NSRect area, NSRect display);
NSRect JardAnnotationControlsRect(NSRect available, NSRect area);
NSPanel *JardAnnotationRegionPanel(NSScreen *screen, void (^complete)(NSRect));
