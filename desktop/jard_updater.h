#import <AppKit/AppKit.h>

BOOL JardVerifyUpdateBundle(NSURL *current, NSURL *candidate, NSString *version, NSString **reason);
BOOL JardSwapUpdateBundles(NSURL *current, NSURL *candidate, NSString **reason);
NSTask *JardScheduleRelaunch(NSURL *bundle, NSString **reason);

@interface JardUpdater : NSObject
- (instancetype)initWithConfiguration:(NSDictionary *)configuration bundle:(NSBundle *)bundle
                         stateChanged:(void (^)(NSDictionary *))changed;
- (NSDictionary *)status;
- (void)check;
- (void)checkAutomaticallyOnActivation:(BOOL)activation;
- (BOOL)selectChannel:(NSString *)channel reason:(NSString **)reason;
- (BOOL)confirmChannelSwitch:(NSString *)digest reason:(NSString **)reason;
- (void)openRelease;
- (BOOL)openStagedReleaseNotesForVersion:(NSString *)version channel:(NSString *)channel reason:(NSString **)reason;
- (void)checkBeforeRestart:(void (^)(void))completion;
- (BOOL)restoreStagedUpdate;
- (BOOL)canInstallStagedUpdate;
- (BOOL)prepareToInstallOnQuit;
- (BOOL)restart:(NSString **)reason;
- (BOOL)installOnQuit:(NSString **)reason;
- (void)cancel;
@end
