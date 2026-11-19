#import <AppKit/AppKit.h>
#import <AVFoundation/AVFoundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <signal.h>
#import <errno.h>
#import <unistd.h>
#import "appkit_host.h"

#define SDK_BYTES(text) (text).UTF8String, [(text) lengthOfBytesUsingEncoding:NSUTF8StringEncoding]

@interface JardHost : NSObject
@property(nonatomic, assign) native_sdk_appkit_host_t *host;
@property(nonatomic, strong) NSURL *bridgeURL;
@property(nonatomic, copy) NSString *origin;
@property(nonatomic, strong) NSTask *bridge;
@property(nonatomic, strong) NSFileHandle *input;
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSString *> *pending;
@property(nonatomic) BOOL showingApp, closing;
@property(nonatomic, weak) NSWindow *window;
@property(nonatomic, strong) id dragMonitor;
@end

@implementation JardHost

- (void)respond:(NSDictionary *)response {
    if ([response[@"id"] isEqual:@"native.browser"]) {
        if (![response[@"ok"] boolValue]) {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Could not open the browser";
            alert.informativeText = response[@"error"][@"message"] ?: @"The local engine is unavailable.";
            [alert beginSheetModalForWindow:NSApp.mainWindow completionHandler:nil];
        }
        return;
    }
    NSData *json = [NSJSONSerialization dataWithJSONObject:response options:0 error:nil];
    if (json && !self.closing) native_sdk_appkit_bridge_respond(self.host, json.bytes, json.length);
}

- (void)reject:(NSString *)request reason:(NSString *)reason {
    [self respond:@{@"id": request, @"ok": @NO, @"error": @{@"code": @"bridge_error", @"message": reason}}];
}

- (void)received:(NSDictionary *)line {
    if (self.closing || ![line isKindOfClass:NSDictionary.class]) return;
    NSDictionary *response = line[@"response"];
    if ([response isKindOfClass:NSDictionary.class] && [response[@"id"] isKindOfClass:NSString.class]) {
        NSString *key = response[@"id"];
        NSString *original = self.pending[key];
        if (!original) return;
        [self.pending removeObjectForKey:key];
        NSMutableDictionary *reply = [response mutableCopy];
        reply[@"id"] = original;
        [self respond:reply];
    } else if ([line[@"navigate"] isEqual:@"app"] && !self.showingApp) {
        // The helper proves engine identity and library ownership before sending this signal.
        // Build the destination locally. A helper message never supplies the navigation URL.
        self.showingApp = YES;
        [self.pending removeAllObjects];
        native_sdk_appkit_load_webview(self.host, SDK_BYTES(self.origin), 1, NULL, 0, NULL, 0, NULL, 0, 0);
    } else if ([line[@"event"] isEqual:@"setup"] || [line[@"event"] isEqual:@"jard-capture"]) {
        NSString *event = line[@"event"];
        NSData *detail = [NSJSONSerialization dataWithJSONObject:line[@"detail"] ?: NSNull.null
                                                         options:NSJSONWritingFragmentsAllowed error:nil];
        if (detail) native_sdk_appkit_emit_window_event(self.host, 1, SDK_BYTES(event), detail.bytes, detail.length);
    }
}

- (BOOL)startBridge:(NSError **)error {
    if (self.bridge.running) return YES;
    NSTask *child = [[NSTask alloc] init];
    child.executableURL = self.bridgeURL;
    NSMutableDictionary *environment = [NSProcessInfo.processInfo.environment mutableCopy];
    environment[@"JARD_UI_EXECUTABLE"] = self.bridgeURL.path;
    environment[@"JARD_UI_INSTANCE"] = @(NSProcessInfo.processInfo.processIdentifier).stringValue;
    child.environment = environment;
    NSPipe *input = NSPipe.pipe;
    NSPipe *output = NSPipe.pipe;
    child.standardInput = input;
    child.standardOutput = output;
    if (![child launchAndReturnError:error]) return NO;
    self.bridge = child;
    self.input = input.fileHandleForWriting;
    NSFileHandle *reader = output.fileHandleForReading;
    // Keep reading status messages off the window thread, including during long model installs.
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{
        NSMutableData *buffer = NSMutableData.data;
        while (YES) {
            @autoreleasepool {
                uint8_t chunk[16384];
                ssize_t count = read(reader.fileDescriptor, chunk, sizeof(chunk));
                if (count < 0 && errno == EINTR) continue;
                if (count <= 0) break;
                [buffer appendBytes:chunk length:(NSUInteger)count];
                const uint8_t *bytes = buffer.bytes;
                NSUInteger consumed = 0;
                for (NSUInteger i = 0; i < buffer.length; i++) {
                    if (bytes[i] != '\n') continue;
                    NSData *line = [buffer subdataWithRange:NSMakeRange(consumed, i - consumed)];
                    consumed = i + 1;
                    id data = [NSJSONSerialization JSONObjectWithData:line options:0 error:nil];
                    if (data) dispatch_async(dispatch_get_main_queue(), ^{ if (self.bridge == child) [self received:data]; });
                }
                if (consumed) [buffer replaceBytesInRange:NSMakeRange(0, consumed) withBytes:NULL length:0];
                if (buffer.length > 4 * 1024 * 1024) break;
            }
        }
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.bridge != child || self.closing) return;
            self.bridge = nil;
            [self.input closeAndReturnError:nil];
            self.input = nil;
            for (NSString *request in self.pending.allValues) [self reject:request reason:@"The local helper stopped. Try the action again."];
            [self.pending removeAllObjects];
        });
    });
    return YES;
}

- (NSDictionary *)chrome {
    double top, left, bottom, right, x, y, width, height;
    native_sdk_appkit_window_chrome_insets(self.host, 1, &top, &left, &bottom, &right, &x, &y, &width, &height);
    return @{@"controlsEnd": @(left), @"controlsCenterY": @(y + height / 2),
             @"fullscreen": @((self.window.styleMask & NSWindowStyleMaskFullScreen) != 0)};
}

- (void)chromeChanged:(NSNotification *)notification {
    NSDictionary *geometry = [self chrome];
    NSData *detail = [NSJSONSerialization dataWithJSONObject:geometry options:0 error:nil];
    native_sdk_appkit_emit_window_event(self.host, 1, SDK_BYTES(@"jard-chrome"), detail.bytes, detail.length);
}

- (void)installWindowChrome {
    self.window = NSApp.mainWindow ?: NSApp.windows.firstObject;
    self.window.titlebarSeparatorStyle = NSTitlebarSeparatorStyleNone;
    self.window.toolbarStyle = NSWindowToolbarStyleUnifiedCompact;
    for (NSString *name in @[NSWindowDidEnterFullScreenNotification, NSWindowDidExitFullScreenNotification]) {
        [NSNotificationCenter.defaultCenter addObserver:self selector:@selector(chromeChanged:) name:name object:self.window];
    }
    __weak JardHost *weakSelf = self;
    self.dragMonitor = [NSEvent addLocalMonitorForEventsMatchingMask:NSEventMaskLeftMouseDown handler:^NSEvent *(NSEvent *event) {
        JardHost *app = weakSelf;
        NSWindow *window = app.window;
        if (!window || event.window != window || window.attachedSheet || NSApp.modalWindow) return event;
        NSPoint point = [window.contentView convertPoint:event.locationInWindow fromView:nil];
        NSRect bounds = window.contentView.bounds;
        CGFloat y = window.contentView.isFlipped ? point.y : NSMaxY(bounds) - point.y;
        // The React header reserves this middle strip; system lights and all app buttons stay outside it.
        if (point.x < 208 || point.x > NSWidth(bounds) - 250 || y < 2 || y > 42) return event;
        if (event.clickCount >= 2) native_sdk_appkit_start_window_drag(app.host, 1);
        else [window performWindowDragWithEvent:event];
        return nil;
    }];
}

// Ask from the signed foreground application so macOS associates the grant with
// the same app the user opened. A status check never requests access.
- (void)checkPermission:(NSString *)source prompt:(BOOL)prompt reply:(void (^)(NSString *))reply {
    if ([source isEqual:@"system"]) {
        if (CGPreflightScreenCaptureAccess()) { reply(@"granted"); return; }
        // A negative CoreGraphics preflight is inconclusive. On explicit request,
        // ask the same ScreenCaptureKit API that the recorder uses. Discard the
        // content list immediately; this check never creates a recording stream.
        if (!prompt) { reply(@"unavailable"); return; }
        [SCShareableContent getShareableContentExcludingDesktopWindows:YES onScreenWindowsOnly:NO
            completionHandler:^(SCShareableContent *content, NSError *error) {
                NSString *status = nil;
                if (content && !error) status = @"granted";
                else if ([error.domain isEqual:SCStreamErrorDomain] && error.code == SCStreamErrorUserDeclined)
                    status = @"denied";
                dispatch_async(dispatch_get_main_queue(), ^{ reply(status); });
            }];
        return;
    }
    switch ([AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio]) {
        case AVAuthorizationStatusAuthorized: reply(@"granted"); return;
        case AVAuthorizationStatusDenied: reply(@"denied"); return;
        case AVAuthorizationStatusRestricted: reply(@"restricted"); return;
        case AVAuthorizationStatusNotDetermined:
            if (!prompt) { reply(@"unavailable"); return; }
            [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL granted) {
                dispatch_async(dispatch_get_main_queue(), ^{ reply(granted ? @"granted" : @"denied"); });
            }];
            return;
    }
    reply(@"unavailable");
}

- (void)invoke:(NSDictionary *)message origin:(NSString *)origin {
    if (self.closing || ![message isKindOfClass:NSDictionary.class]) return;
    NSString *request = message[@"id"];
    NSString *command = message[@"command"];
    if (![request isKindOfClass:NSString.class] || request.length > 100 || ![command isKindOfClass:NSString.class]) return;
    if ([command isEqual:@"jard.chrome"] && [origin isEqual:(self.showingApp ? self.origin : @"zero://inline")]) {
        [self respond:@{@"id": request, @"ok": @YES, @"result": [self chrome]}];
        return;
    }
    if (self.showingApp && [origin isEqual:self.origin] &&
        ([command isEqual:@"jard.permission"] || [command isEqual:@"jard.openPrivacy"])) {
        NSDictionary *payload = message[@"payload"];
        NSString *source = [payload isKindOfClass:NSDictionary.class] ? payload[@"source"] : nil;
        if (![@[@"mic", @"system"] containsObject:source ?: @""]) {
            [self reject:request reason:@"Unknown recording permission."];
            return;
        }
        if ([command isEqual:@"jard.permission"]) {
            [self checkPermission:source prompt:[payload[@"prompt"] isEqual:@YES] reply:^(NSString *status) {
                if (status) [self respond:@{@"id": request, @"ok": @YES, @"result": @{@"status": status}}];
                else [self reject:request reason:@"Mac audio access could not be checked. Try again."];
            }];
        } else {
            NSString *pane = [source isEqual:@"mic"] ? @"Privacy_Microphone" : @"Privacy_ScreenCapture";
            NSString *address = [@"x-apple.systempreferences:com.apple.preference.security?" stringByAppendingString:pane];
            if ([NSWorkspace.sharedWorkspace openURL:[NSURL URLWithString:address]]) {
                [self respond:@{@"id": request, @"ok": @YES, @"result": @{}}];
            } else {
                [self reject:request reason:@"Open System Settings and select Privacy & Security."];
            }
        }
        return;
    }
    if (self.showingApp && [origin isEqual:self.origin] && [command isEqual:@"jard.revealApp"]) {
        [NSWorkspace.sharedWorkspace activateFileViewerSelectingURLs:@[NSBundle.mainBundle.bundleURL]];
        [self respond:@{@"id": request, @"ok": @YES, @"result": @{}}];
        return;
    }
    NSArray *commands = self.showingApp
        ? @[@"jard.devices", @"jard.record", @"jard.stop", @"jard.dictation", @"jard.live", @"jard.openAudio", @"jard.export", @"jard.recover", @"jard.discard", @"jard.open"]
        : @[@"jard.prepare", @"jard.setup"];
    if (![origin isEqual:(self.showingApp ? self.origin : @"zero://inline")] || ![commands containsObject:command]) {
        [self reject:request reason:@"This command is unavailable in this view."];
        return;
    }
    NSError *error = nil;
    if (![self startBridge:&error]) {
        [self reject:request reason:error.localizedDescription ?: @"The app could not start its local helper."];
        return;
    }
    NSString *wireID = NSUUID.UUID.UUIDString;
    NSData *json = [NSJSONSerialization dataWithJSONObject:@{@"id": wireID, @"command": command,
                                                        @"payload": message[@"payload"] ?: @{}} options:0 error:&error];
    if (!json || json.length > 65536 || self.pending.count >= 64) {
        [self reject:request reason:@"The app is busy or the request is too large."];
        return;
    }
    self.pending[wireID] = request;
    NSMutableData *line = [json mutableCopy];
    [line appendBytes:"\n" length:1];
    if (![self.input writeData:line error:&error]) {
        [self.pending removeObjectForKey:wireID];
        [self reject:request reason:error.localizedDescription ?: @"The local helper disconnected."];
    }
}

- (void)openBrowser:(id)sender {
    [self invoke:@{@"id": @"native.browser", @"command": @"jard.open", @"payload": @{}} origin:self.origin];
}

- (void)toggleAutomaticUpdates:(NSMenuItem *)item {
    BOOL enabled = item.state != NSControlStateValueOn;
    [NSUserDefaults.standardUserDefaults setBool:enabled forKey:@"JardCheckForUpdatesOnLaunch"];
    item.state = enabled ? NSControlStateValueOn : NSControlStateValueOff;
}

- (BOOL)validateMenuItem:(NSMenuItem *)item {
    if (item.action == @selector(toggleAutomaticUpdates:)) return YES;
    return self.showingApp && !self.closing;
}

- (void)shutdown {
    self.closing = YES;
    if (self.dragMonitor) { [NSEvent removeMonitor:self.dragMonitor]; self.dragMonitor = nil; }
    [NSNotificationCenter.defaultCenter removeObserver:self];
    [self.input closeAndReturnError:nil];
    self.input = nil;
    // EOF lets the helper finalize any accepted audio and release its client lease.
}
@end

static void bridgeMessage(void *context, uint64_t window, const char *label, size_t labelLength,
                          const char *data, size_t length, const char *origin, size_t originLength) {
    if (window != 1 || labelLength != 4 || memcmp(label, "main", 4) || length > 131072) return;
    JardHost *app = (__bridge JardHost *)context;
    NSData *json = [NSData dataWithBytes:data length:length];
    NSString *source = [[NSString alloc] initWithBytes:origin length:originLength encoding:NSUTF8StringEncoding];
    [app invoke:[NSJSONSerialization JSONObjectWithData:json options:0 error:nil] origin:source];
}

static void hostEvent(void *context, const native_sdk_appkit_event_t *event) {
    if (event->kind == NATIVE_SDK_APPKIT_EVENT_SHUTDOWN) [(__bridge JardHost *)context shutdown];
}

int main(void) {
    @autoreleasepool {
        signal(SIGPIPE, SIG_IGN);
        NSBundle *bundle = NSBundle.mainBundle;
        NSURL *resources = bundle.resourceURL;
        NSString *title = [bundle objectForInfoDictionaryKey:@"CFBundleDisplayName"] ?: @"Jard Soundfolio Local";
        NSString *identifier = bundle.bundleIdentifier ?: @"local.jardaudio.desktop";
        JardHost *app = [[JardHost alloc] init];
        app.pending = NSMutableDictionary.dictionary;
        app.bridgeURL = [[bundle.bundleURL URLByAppendingPathComponent:@"Contents/Helpers"] URLByAppendingPathComponent:@"jard-ui"];
        NSString *port = NSProcessInfo.processInfo.environment[@"RUMBLE_PORT"] ?: @"8765";
        NSInteger portNumber = port.integerValue;
        if (portNumber < 1 || portNumber > 65535 || ![port isEqual:@(portNumber).stringValue]) return 1;
        app.origin = [NSString stringWithFormat:@"http://127.0.0.1:%ld", (long)portNumber];
        NSURL *setupURL = [resources URLByAppendingPathComponent:@"setup.html"];
#ifdef JARD_TESTING
        NSDictionary *env = NSProcessInfo.processInfo.environment;
        if (env[@"JARD_TEST_HELPER"]) app.bridgeURL = [NSURL fileURLWithPath:env[@"JARD_TEST_HELPER"]];
        if (env[@"JARD_TEST_SETUP"]) setupURL = [NSURL fileURLWithPath:env[@"JARD_TEST_SETUP"]];
#endif
        NSString *setup = [NSString stringWithContentsOfURL:setupURL encoding:NSUTF8StringEncoding error:nil];
        if (!setup) setup = @"<p>The setup page is missing. Reinstall Jard Soundfolio Local.</p>";
        NSString *version = [bundle objectForInfoDictionaryKey:@"CFBundleVersion"] ?: @"0.0.0";
        app.host = native_sdk_appkit_create(SDK_BYTES(title), SDK_BYTES(title), SDK_BYTES(version),
            SDK_BYTES(@"Private local audio transcription"), 1, 0, 1, SDK_BYTES(title), SDK_BYTES(identifier),
            NULL, 0, SDK_BYTES(@"main"), 0, 0, 1050, 760, 0, 2, 1, 1, 2 /* hidden_inset_tall */, 0, 0);
        if (!app.host) return 1;
        NSDictionary *updates = [bundle objectForInfoDictionaryKey:@"JardUpdates"];
        NSString *feed = updates[@"feed_url"];
        NSString *key = updates[@"public_key"];
        if (feed.length && key.length) {
            NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
            [defaults registerDefaults:@{@"JardCheckForUpdatesOnLaunch": updates[@"check_on_start"] ?: @NO}];
            BOOL automatic = [defaults boolForKey:@"JardCheckForUpdatesOnLaunch"];
            native_sdk_appkit_configure_updates(app.host, SDK_BYTES(feed), SDK_BYTES(key), automatic, SDK_BYTES(@"macos-aarch64"));
            NSMenu *menu = NSApp.mainMenu.itemArray.firstObject.submenu;
            NSMenuItem *toggle = [menu insertItemWithTitle:@"Check for Updates on Launch"
                action:@selector(toggleAutomaticUpdates:) keyEquivalent:@"" atIndex:2];
            toggle.target = app;
            toggle.state = automatic ? NSControlStateValueOn : NSControlStateValueOff;
        }
        [app installWindowChrome];
        NSMenu *file = [NSApp.mainMenu itemWithTitle:@"File"].submenu;
        NSMenuItem *open = [file insertItemWithTitle:@"Open in Browser" action:@selector(openBrowser:) keyEquivalent:@"b" atIndex:0];
        open.keyEquivalentModifierMask = NSEventModifierFlagCommand | NSEventModifierFlagShift;
        open.target = app;
        native_sdk_appkit_set_window_content_min_size(app.host, 1, 740, 500);
        NSString *origins = [@"zero://inline\n" stringByAppendingString:app.origin];
        native_sdk_appkit_set_security_policy(app.host, SDK_BYTES(origins), NULL, 0, 0);
        native_sdk_appkit_set_bridge_callback(app.host, bridgeMessage, (__bridge void *)app);
        native_sdk_appkit_load_webview(app.host, SDK_BYTES(setup), 0, NULL, 0, NULL, 0, NULL, 0, 0);
        native_sdk_appkit_run(app.host, hostEvent, (__bridge void *)app);
        [app shutdown];
        native_sdk_appkit_destroy(app.host);
        return 0;
    }
}
