#import <AppKit/AppKit.h>

typedef void (^JardDictationReply)(id result, NSString *error);
typedef void (^JardDictationSend)(NSString *command, NSDictionary *payload, JardDictationReply reply);
typedef void (^JardPasteExcerpt)(NSString *text, void (^didPaste)(BOOL inserted));
typedef void (^JardPrepareExcerpt)(JardPasteExcerpt ready);
typedef BOOL (^JardAnnotationInput)(CGEventType type, CGEventRef event, CGKeyCode shortcutCode, BOOL chordFree);

int JardLockDictationPreferences(BOOL exclusive);
int JardApplyOfflineDictationSettings(void);

@interface JardSystemDictation : NSObject
@property(nonatomic, copy) void (^preferencesChanged)(NSDictionary *status);
@property(nonatomic, copy) void (^microphonePreferenceChanged)(NSString *uid);
@property(nonatomic, copy) void (^phaseChanged)(NSString *phase);
@property(nonatomic, copy) void (^activityChanged)(void);
- (instancetype)initWithSend:(JardDictationSend)send;
- (void)workspaceReady;
- (void)applicationActivationPolicyChanged;
- (void)stopSoundPreview;
- (BOOL)keepsRunning;
- (BOOL)quietAtLogin;
- (BOOL)hasActivity;
- (void)configurePasteLastMenuItem:(NSMenuItem *)item;
- (void)pasteLastFromMenu;
- (NSDictionary *)sdkMicrophoneSettings;
- (void)setSDKCaptureActive:(BOOL)active;
- (void)setMeetingPasteAction:(void (^)(void))action;
- (void)setMeetingPasteAvailable:(BOOL)available;
- (void)setAnnotationInput:(JardAnnotationInput)input;
- (void)setAnnotationActive:(BOOL)active;
- (NSRunningApplication *)annotationPasteDestination;
- (NSDictionary *)annotationOriginSnapshotAllowingPrevious:(BOOL)allowPrevious;
- (NSUInteger)annotationPreparationRevision;
- (BOOL)annotationRoutingDisplayAvailable;
- (void)pasteAnnotation:(NSString *)markdown image:(NSData *)image path:(NSString *)path
    destination:(NSRunningApplication *)destination preparationRevision:(NSUInteger)revision
    clipboardRevision:(NSInteger)clipboardRevision reply:(JardDictationReply)reply;
- (void)enqueueMeetingPaste:(JardPrepareExcerpt)prepare;
- (void)pauseForUpdate:(BOOL)paused;
- (void)helperDisconnected;
- (BOOL)handleCapture:(NSDictionary *)event;
- (void)updateWarmInputStatus:(NSDictionary *)event;
- (NSDictionary *)status;
- (void)request:(NSDictionary *)parameters reply:(JardDictationReply)reply;
- (void)controlSettings:(NSDictionary *)parameters reply:(JardDictationReply)reply;
- (void)control:(NSString *)action reply:(JardDictationReply)reply;
- (void)shutdown;
@end
