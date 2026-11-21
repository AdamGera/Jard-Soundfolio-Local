#import <AppKit/AppKit.h>
#import <Carbon/Carbon.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>
#import "../jard_interface_language.h"
#import "../jard_updater.h"
#import "../system_dictation.h"

@interface JardHost : NSObject
@property(nonatomic, readonly) NSWindow *window;
@property(nonatomic, readonly) BOOL showingApp;
@property(nonatomic, readonly) NSStatusItem *meetingStatusItem;
@property(nonatomic, readonly) NSDictionary<NSString *, NSMenuItem *> *meetingMenuItems;
@property(nonatomic, readonly) NSMenuItem *updateMenu;
@property(nonatomic, readonly) JardUpdater *updater;
@property(nonatomic, readonly) JardSystemDictation *systemDictation;
@property(nonatomic, readonly) BOOL annotationManaged;
- (void)completeWindowLaunch:(NSAppleEventDescriptor *)event;
- (void)setShowDockIcon:(BOOL)enabled;
@property(nonatomic, readonly) NSDictionary *windowNavigation;
- (void)controlWindowAction:(NSString *)action pane:(NSString *)pane reply:(void (^)(id result, NSString *error))reply;
- (NSDictionary *)dockIconStatus;
- (void)refreshInterfaceLanguage;
- (NSDictionary *)workspaceUpdateStatus:(NSDictionary *)status;
- (void)performUpdateAction:(NSString *)action payload:(NSDictionary *)payload reply:(JardDictationReply)reply;
- (void)refreshMeetingAvailability;
- (void)updateChanged:(NSDictionary *)status;
- (BOOL)canStartAnnotation;
- (void)controlAnnotation:(NSString *)action parameters:(NSDictionary *)parameters reply:(JardDictationReply)reply;
@end

static NSString *scenario;
static NSURL *fixtureRoot;
static __weak JardHost *fixtureHost;
static void (*normalLaunch)(JardHost *, SEL, NSNotification *);
static void (*normalMeetingMenu)(JardHost *, SEL);
static NSDictionary *(*normalUpdateStatus)(JardUpdater *, SEL);
static BOOL stagedRefresh;

static NSDictionary *fixtureUpdateStatus(JardUpdater *updater, SEL selector) {
    if (stagedRefresh) return @{@"phase": @"checking", @"staged": @YES, @"version": @"2"};
    return normalUpdateStatus(updater, selector);
}

static void fixtureMeetingMenu(JardHost *host, SEL selector) {
    if (![scenario isEqual:@"dock-unavailable"]) normalMeetingMenu(host, selector);
}

static void report(NSString *name, NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:nil];
    [data writeToURL:[fixtureRoot URLByAppendingPathComponent:name] atomically:YES];
}

static WKWebView *webView(NSView *view) {
    if ([view isKindOfClass:WKWebView.class]) return (WKWebView *)view;
    for (NSView *child in view.subviews) {
        WKWebView *web = webView(child);
        if (web) return web;
    }
    return nil;
}

static void fixtureLaunch(JardHost *host, SEL selector, NSNotification *notification) {
    fixtureHost = host;
    BOOL visibleBeforeLaunch = host.window.visible;
    if ([scenario isEqual:@"normal"] || [scenario isEqual:@"annotation-managed"] || [scenario isEqual:@"managed-updates"] ||
        [scenario hasPrefix:@"locale-"] || [scenario isEqual:@"dock-setting"] || [scenario isEqual:@"dock-unavailable"]) {
        normalLaunch(host, selector, notification);
    } else {
        NSAppleEventDescriptor *event = [NSAppleEventDescriptor appleEventWithEventClass:kCoreEventClass
            eventID:kAEOpenApplication targetDescriptor:[NSAppleEventDescriptor nullDescriptor]
            returnID:kAutoGenerateReturnID transactionID:kAnyTransactionID];
        [event setParamDescriptor:[NSAppleEventDescriptor descriptorWithEnumCode:keyAELaunchedAsLogInItem]
                       forKeyword:keyAEPropData];
        [host completeWindowLaunch:event];
        if (![scenario isEqual:@"login-idle"]) {
            [NSNotificationCenter.defaultCenter postNotificationName:NSApplicationDidBecomeActiveNotification object:NSApp];
        }
    }
    report(@"launched.json", @{@"visibleBeforeLaunch": @(visibleBeforeLaunch), @"visible": @(host.window.visible)});
}

static BOOL fixtureQuietAtLogin(id self, SEL selector) {
    return ![scenario isEqual:@"login-input"] || ![[self valueForKey:@"workspace"] boolValue];
}

static BOOL fixtureAnnotationManaged(id self, SEL selector) { return YES; }

@interface JardWindowFixture : NSObject
@end

@implementation JardWindowFixture

+ (void)load {
    if (![NSBundle.mainBundle.bundleIdentifier hasPrefix:@"local.jardaudio.window-fixture."]) return;
    NSDictionary *env = NSProcessInfo.processInfo.environment;
    NSString *root = env[@"JARD_WINDOW_FIXTURE_ROOT"];
    scenario = env[@"JARD_WINDOW_FIXTURE_SCENARIO"];
    if (!root.length || !scenario.length) return;
    fixtureRoot = [NSURL fileURLWithPath:root isDirectory:YES];
    Method launch = class_getInstanceMethod(NSClassFromString(@"JardHost"), NSSelectorFromString(@"applicationStarted:"));
    normalLaunch = (void (*)(JardHost *, SEL, NSNotification *))method_setImplementation(launch, (IMP)fixtureLaunch);
    if ([scenario isEqual:@"annotation-managed"]) {
        Method policy = class_getInstanceMethod(NSClassFromString(@"JardHost"), @selector(annotationManaged));
        method_setImplementation(policy, (IMP)fixtureAnnotationManaged);
    }
    if ([scenario isEqual:@"dock-unavailable"]) {
        Method menu = class_getInstanceMethod(NSClassFromString(@"JardHost"), NSSelectorFromString(@"createMeetingMenu"));
        normalMeetingMenu = (void (*)(JardHost *, SEL))method_setImplementation(menu, (IMP)fixtureMeetingMenu);
    }
    if ([scenario isEqual:@"dock-updates"]) {
        Method status = class_getInstanceMethod(JardUpdater.class, @selector(status));
        normalUpdateStatus = (NSDictionary *(*)(JardUpdater *, SEL))method_setImplementation(status, (IMP)fixtureUpdateStatus);
    }
    if (![scenario isEqual:@"login-setup"]) {
        Method quiet = class_getInstanceMethod(NSClassFromString(@"JardSystemDictation"), NSSelectorFromString(@"quietAtLogin"));
        method_setImplementation(quiet, (IMP)fixtureQuietAtLogin);
    }

    dispatch_async(dispatch_get_main_queue(), ^{
        NSURL *path = [fixtureRoot URLByAppendingPathComponent:@"command"];
        __block NSPanel *overlay;
        NSTimer *poll = [NSTimer timerWithTimeInterval:.03 repeats:YES block:^(NSTimer *timer) {
            JardHost *host = fixtureHost;
            if (!host) return;
            NSString *command = [NSString stringWithContentsOfURL:path encoding:NSUTF8StringEncoding error:nil];
            if (!command.length || ![NSFileManager.defaultManager removeItemAtURL:path error:nil]) return;
            NSWindow *window = host.window;
            if ([command isEqual:@"close"]) [window performClose:nil];
            else if ([command isEqual:@"activate"]) {
                [NSNotificationCenter.defaultCenter postNotificationName:NSApplicationDidBecomeActiveNotification object:NSApp];
            } else if ([command isEqual:@"dock"]) {
                [NSApp.delegate applicationShouldHandleReopen:NSApp hasVisibleWindows:NO];
            } else if ([command isEqual:@"dock-off"]) [host setShowDockIcon:NO];
            else if ([command isEqual:@"dock-on"]) [host setShowDockIcon:YES];
            else if ([command isEqual:@"menu-off"]) host.meetingStatusItem.visible = NO;
            else if ([command isEqual:@"menu-on"]) host.meetingStatusItem.visible = YES;
            else if ([command isEqual:@"menu-live-meeting"]) {
                __block NSString *idleFailure = nil;
                [host controlWindowAction:@"window-show" pane:@"live-meeting" reply:^(id result, NSString *error) { idleFailure = error; }];
                id meeting = [host valueForKey:@"meetings"];
                [meeting setValue:@"recording" forKey:@"phase"];
                NSMenuItem *open = host.meetingMenuItems[@"open"];
                NSInteger index = [open.menu indexOfItem:open];
                if (index >= 0) [open.menu performActionForItemAtIndex:index];
                report(@"reply.json", @{@"idleFailure": idleFailure ?: @"", @"pane": host.windowNavigation[@"pane"] ?: @"",
                    @"recording": @([[meeting valueForKey:@"phase"] isEqual:@"recording"]), @"visible": @(window.visible)});
                [meeting setValue:@"idle" forKey:@"phase"];
                return;
            }
            else if ([command isEqual:@"menu-open"]) {
                NSMenuItem *open = host.meetingMenuItems[@"open"];
                NSInteger index = [open.menu indexOfItem:open];
                if (index >= 0) [open.menu performActionForItemAtIndex:index];
            } else if ([command isEqual:@"application-check"] || [command isEqual:@"status-check"]) {
                NSMenuItem *item = [command isEqual:@"application-check"] ? host.updateMenu : host.meetingMenuItems[@"update"];
                NSMenu *menu = item.menu;
                [menu update];
                NSInteger index = item && menu ? [menu indexOfItem:item] : -1;
                if (index >= 0 && !item.hidden && item.enabled) [menu performActionForItemAtIndex:index];
            } else if ([command isEqual:@"annotation-policy"]) {
                NSMutableDictionary *failures = NSMutableDictionary.dictionary;
                if (host.annotationManaged) {
                    for (NSString *source in @[@"display", @"window", @"area", @"phone"]) {
                        [host controlAnnotation:@"start" parameters:@{@"source": source} reply:^(id state, NSString *error) {
                            failures[source] = error ?: @"Capture started unexpectedly";
                        }];
                    }
                }
                [host controlAnnotation:@"status" parameters:@{} reply:^(id state, NSString *error) {
                    report(@"reply.json", @{@"state": state ?: @{}, @"startFailures": failures,
                        @"shortcutRegistered": @([host.systemDictation valueForKey:@"annotationEvents"] != nil),
                        @"canStart": @([host canStartAnnotation])});
                }];
                return;
            } else if ([command isEqual:@"paste-menu"]) {
                NSMenuItem *item = host.meetingMenuItems[@"pasteLast"];
                [host.meetingStatusItem.menu update];
                report(@"reply.json", @{@"title": item.title ?: @"", @"shortcut": item.keyEquivalent ?: @"",
                    @"shown": @(item && !item.hidden), @"enabled": @(item.enabled),
                    @"dockVisible": @(NSApp.activationPolicy == NSApplicationActivationPolicyRegular)});
                return;
            } else if ([command isEqual:@"background-check"]) {
                [host.updater check];
            } else if ([command isEqual:@"cli-hide"] || [command isEqual:@"cli-show"]) {
                BOOL hide = [command isEqual:@"cli-hide"];
                [host controlWindowAction:hide ? @"window-hide" : @"window-show" pane:hide ? nil : @"history"
                    reply:^(id result, NSString *error) {
                        report(@"reply.json", @{@"visible": @(window.visible), @"minimized": @(window.miniaturized),
                            @"dockVisible": @(NSApp.activationPolicy == NSApplicationActivationPolicyRegular),
                            @"result": result ?: @{}, @"error": error ?: @"", @"navigation": host.windowNavigation ?: @{}});
                    }];
                return;
            } else if ([command isEqual:@"update-policy"]) {
                NSMenu *menu = NSApp.mainMenu.itemArray.firstObject.submenu;
                NSMenuItem *item = host.meetingMenuItems[@"update"];
                [host.meetingStatusItem.menu update];
                NSUInteger visibleActions = 0;
                for (NSMenuItem *choice in menu.itemArray) {
                    if (!choice.hidden && (choice.action == NSSelectorFromString(@"updateFromMenu:") ||
                        choice.action == NSSelectorFromString(@"toggleAutomaticUpdates:"))) visibleActions++;
                }
                NSMutableDictionary *failures = NSMutableDictionary.dictionary;
                for (NSString *action in @[@"check", @"channel", @"confirmSwitch", @"restart", @"download", @"releaseNotes"]) {
                    [host performUpdateAction:action payload:@{} reply:^(id result, NSString *error) {
                        failures[action] = error ?: @"Unexpectedly accepted";
                    }];
                }
                report(@"reply.json", @{@"updaterCreated": @(host.updater != nil), @"update": [host workspaceUpdateStatus:host.updater.status],
                    @"statusMenuTitle": item.title ?: @"", @"statusMenuShown": @(item && !item.hidden),
                    @"statusMenuEnabled": @(item.enabled), @"visibleActions": @(visibleActions), @"failures": failures});
                return;
            } else if ([command isEqual:@"update-hold"] || [command isEqual:@"update-unhold"]) {
                if (!stagedRefresh) {
                    stagedRefresh = YES;
                    [host updateChanged:host.updater.status];
                }
                [host setValue:@([command isEqual:@"update-hold"]) forKey:@"otherRecording"];
                [host refreshMeetingAvailability];
                NSMenuItem *item = host.meetingMenuItems[@"update"];
                report(@"reply.json", @{@"update": [host workspaceUpdateStatus:host.updater.status],
                    @"publishedHold": [host valueForKey:@"updateRestartBlocked"], @"statusMenuEnabled": @(item.enabled)});
                return;
            } else if ([command isEqual:@"update-status"]) {
                report(@"reply.json", @{@"update": [host workspaceUpdateStatus:host.updater.status],
                    @"visible": @(window.visible), @"sheet": @(window.attachedSheet != nil),
                    @"statusMenuTitle": host.meetingMenuItems[@"update"].title ?: @""});
                return;
            } else if ([command isEqual:@"preview-active"]) {
                [host.systemDictation setValue:NSMutableArray.array forKey:@"previewSounds"];
            } else if ([command isEqual:@"minimize"]) [window miniaturize:nil];
            else if ([command isEqual:@"overlay"]) {
                overlay = [[NSPanel alloc] initWithContentRect:NSMakeRect(40, 40, 160, 90)
                    styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
                [overlay makeKeyAndOrderFront:nil];
            } else if ([command isEqual:@"dismiss-overlay"]) {
                [overlay orderOut:nil];
                overlay = nil;
            } else if ([command isEqual:@"setup-title"]) {
                [webView(window.contentView) evaluateJavaScript:@"({title: document.getElementById('title')?.textContent ?? '', lang: document.documentElement.lang})"
                    completionHandler:^(id result, NSError *error) {
                        report(@"reply.json", [result isKindOfClass:NSDictionary.class] ? result : @{@"title": @""});
                    }];
                return;
            } else if ([command isEqual:@"locale-test"]) {
                NSDictionary *status = JardInterfaceLanguageStatus(), *choices = status[@"choices"];
                NSArray *tags = @[@"zz-YY", @"pl-PL", @"zh_TW.UTF-8", @"zh-Hans-HK", @"zh-Hant", @"pt-PT"];
                NSMutableArray *resolved = NSMutableArray.new;
                for (NSString *tag in tags) [resolved addObject:JardResolveInterfaceLanguage(tag, choices)];
                BOOL frenchMenu = [NSApp.mainMenu itemWithTitle:@"Fichier"] != nil;
                BOOL invalidRejected = !JardSetInterfaceLanguage(@"zz");
                BOOL japaneseAccepted = JardSetInterfaceLanguage(@"ja");
                [host refreshInterfaceLanguage];
                report(@"reply.json", @{@"frMenu": @(frenchMenu), @"jaMenu": @([NSApp.mainMenu itemWithTitle:@"ファイル"] != nil),
                    @"resolved": resolved, @"invalidRejected": @(invalidRejected), @"accepted": @(japaneseAccepted),
                    @"selected": JardInterfaceLanguageStatus()[@"selected"]});
                return;
            }
            report(@"reply.json", @{@"visible": @(window.visible), @"ready": @(host.showingApp),
                @"minimized": @(window.miniaturized), @"overlayFocused": @(overlay && NSApp.keyWindow == overlay),
                @"soundPreview": host.systemDictation.status[@"soundPreview"],
                @"dockVisible": @(NSApp.activationPolicy == NSApplicationActivationPolicyRegular),
                @"dockSetting": [host dockIconStatus], @"quitAvailable": @(host.meetingMenuItems[@"quit"] && !host.meetingMenuItems[@"quit"].hidden)});
        }];
        [NSRunLoop.mainRunLoop addTimer:poll forMode:NSRunLoopCommonModes];
    });
}

@end
