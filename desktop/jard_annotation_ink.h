#import <AppKit/AppKit.h>

NSRect JardAnnotationAppKitRect(CGRect quartz, CGFloat primaryHeight);
NSDictionary *JardAnnotationRoutingOrigin(NSDictionary *snapshot, NSDictionary *selectedApp, pid_t selectedPID,
    NSString *selectedTitle, CGRect captured);
NSPoint JardAnnotationUnitPoint(NSPoint point, NSSize bounds);
NSDictionary *JardAnnotationEdge(NSPoint from, NSPoint to, NSTimeInterval time);
void JardRenderAnnotation(NSURL *input, NSURL *output, NSArray<NSDictionary *> *events, void (^completion)(NSError *));
NSArray<NSNumber *> *JardAnnotationFrameTimes(NSArray<NSDictionary *> *events, NSTimeInterval duration);
void JardWriteAnnotationAssets(NSURL *movie, NSString *source, NSDictionary *origin, NSArray<NSDictionary *> *events, void (^completion)(NSError *));
