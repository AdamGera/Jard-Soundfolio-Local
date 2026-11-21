#import <Foundation/Foundation.h>

typedef NS_ENUM(NSUInteger, JardPlaybackState) {
    JardPlaybackPlaying,
    JardPlaybackPaused,
};

@interface JardPlayback : NSObject
@property(nonatomic, readonly) JardPlaybackState state;
@property(nonatomic, readonly, copy) NSString *item;
@property(nonatomic, readonly) double position;
- (instancetype)initWithState:(JardPlaybackState)state item:(NSString *)item position:(double)position;
@end

@protocol JardMediaPlayer <NSObject>
@property(nonatomic, readonly, copy) NSString *name;
- (BOOL)requestAccess;
- (JardPlayback *)playback;
- (BOOL)pause;
- (BOOL)resume;
@end

@interface JardDictationMedia : NSObject
- (instancetype)initWithPlayers:(NSArray<id<JardMediaPlayer>> * (^)(void))runningPlayers;
- (void)requestAccess:(void (^)(NSArray<NSString *> *allowed, NSArray<NSString *> *unavailable))completion;
- (void)begin;
- (void)finish;
- (void)shutdown;
@end
