#import <AppKit/AppKit.h>

BOOL JardValidMeetingCorner(NSString *corner);
NSString *JardNearestMeetingCorner(NSRect visible, NSPoint center);
NSRect JardMeetingCornerFrame(NSRect visible, NSSize size, NSString *corner);

@interface JardMeetingPanel : NSPanel
@property(nonatomic, copy) void (^placementChanged)(NSString *corner, NSScreen *screen);
@property(nonatomic, readonly, getter=isDragging) BOOL dragging;
- (void)moveToCorner:(NSString *)corner screen:(NSScreen *)screen animated:(BOOL)animated;
- (void)beginDragAt:(NSPoint)point time:(NSTimeInterval)time;
- (void)dragTo:(NSPoint)point time:(NSTimeInterval)time;
- (void)endDragAt:(NSPoint)point time:(NSTimeInterval)time;
- (void)cancelDrag;
- (BOOL)reduceMotion;
@end

@interface JardMeetingSurface : NSVisualEffectView
@end

@interface JardMeetingCaption : NSTextField
@end
