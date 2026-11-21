#import <AppKit/AppKit.h>

typedef NS_ENUM(NSInteger, JardIndicatorStage) {
    JardIndicatorRecording,
    JardIndicatorProcessing,
    JardIndicatorDone,
    JardIndicatorFailed,
};

@interface JardDictationMeter : NSView
- (void)setStage:(JardIndicatorStage)stage label:(NSString *)label;
- (void)advanceAt:(NSTimeInterval)time rms:(double)rms;
- (void)beginDismissal;
- (void)dismiss;
@end
