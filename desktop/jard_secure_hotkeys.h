#import <Foundation/Foundation.h>
#import <ApplicationServices/ApplicationServices.h>

typedef NS_ENUM(NSUInteger, JardSecureAction) {
    JardSecureAnnotate,
    JardSecureMeetingPaste,
    JardSecurePasteLast,
};

typedef NS_ENUM(NSUInteger, JardSecureEdge) {
    JardSecurePressed,
    JardSecureReleased,
    JardSecureCancelled,
};

typedef struct {
    JardSecureAction action;
    NSInteger code;
    CGEventFlags flags;
} JardSecureChord;

typedef void (^JardSecureHotkeyHandler)(JardSecureChord chord, JardSecureEdge edge);

@interface JardSecureHotkeys : NSObject
- (instancetype)initWithHandler:(JardSecureHotkeyHandler)handler;
- (void)update:(const JardSecureChord *)chords count:(NSUInteger)count;
@end
