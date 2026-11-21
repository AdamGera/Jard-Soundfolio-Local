#import <AppKit/AppKit.h>
#import "system_dictation.h"

BOOL JardCopyAnnotationHandoff(NSPasteboard *board, NSInteger expectedChangeCount, NSString *markdown, NSData *png, BOOL *includedImage);

@interface JardAnnotation : NSObject
- (instancetype)initWithSend:(JardDictationSend)send stateChanged:(void (^)(NSDictionary *))changed;
- (NSDictionary *)status;
- (BOOL)busy;
- (void)start:(NSArray<NSString *> *)languages source:(NSString *)source microphone:(NSString *)microphone origin:(NSDictionary *)origin reply:(JardDictationReply)reply;
- (BOOL)handleEvent:(CGEventType)type event:(CGEventRef)event shortcutCode:(CGKeyCode)shortcutCode
           allowStart:(BOOL)allowStart start:(dispatch_block_t)start;
- (BOOL)stop;
- (BOOL)cancel;
- (void)reveal;
- (void)shutdown;
@end
