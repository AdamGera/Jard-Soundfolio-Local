#import <AppKit/AppKit.h>

@interface JardAnnotationRoutePanel : NSObject
- (instancetype)initWithAvailability:(BOOL (^)(void))available
    action:(void (^)(NSString *capture, NSString *action, BOOL held))action;
- (void)update:(NSDictionary *)detail;
- (void)dismiss;
@end
