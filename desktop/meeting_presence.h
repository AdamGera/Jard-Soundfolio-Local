#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

@interface JardCall : NSObject
@property(nonatomic, copy) NSString *provider;
@property(nonatomic, copy) NSString *application;
@property(nonatomic, copy) NSString *label;
@property(nonatomic, copy) NSString *identity;
@property(nonatomic) BOOL slow;
@property(nonatomic) BOOL unnamedBrowser;
@property(nonatomic) BOOL inputActive;
@end

// nil represents an unavailable observation; an empty array is a successful read.
NSArray<JardCall *> * _Nullable JardActiveCalls(BOOL includeOutput);
JardCall * _Nullable JardMeetingBrowserCall(NSURL *url, NSString *application);
JardCall *JardMeetingUnnamedBrowser(NSString *application, NSString *name);
JardCall * _Nullable JardMeetingDirectCall(NSString *bundle);
NSArray<NSDictionary<NSString *, NSString *> *> *JardMeetingProviderCatalog(void);

@interface JardMeetingPresence : NSObject
- (nullable JardCall *)offerFromCalls:(NSArray<JardCall *> *)calls now:(NSTimeInterval)now;
- (void)snooze:(JardCall *)call absence:(NSTimeInterval)seconds now:(NSTimeInterval)now;
- (void)resetObservations;
@end

NS_ASSUME_NONNULL_END
