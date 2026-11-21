#import "meeting_presence.h"
#import <AppKit/AppKit.h>
#import <ApplicationServices/ApplicationServices.h>
#import <CoreAudio/CoreAudio.h>
#import <libproc.h>

@implementation JardCall
@end

NSArray<NSDictionary<NSString *, NSString *> *> *JardMeetingProviderCatalog(void) {
    static NSArray *catalog;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        catalog = @[
            @{@"provider": @"app:slack", @"label": @"Slack"},
            @{@"provider": @"app:zoom", @"label": @"Zoom"},
            @{@"provider": @"site:meet.google.com", @"label": @"Google Meet"},
            @{@"provider": @"app:microsoft teams", @"label": @"Microsoft Teams"},
            @{@"provider": @"app:facetime", @"label": @"FaceTime"},
            @{@"provider": @"app:whatsapp", @"label": @"WhatsApp"},
            @{@"provider": @"app:gather", @"label": @"Gather"},
            @{@"provider": @"app:discord", @"label": @"Discord"},
            @{@"provider": @"app:signal", @"label": @"Signal"},
            @{@"provider": @"app:telegram", @"label": @"Telegram"},
            @{@"provider": @"app:wechat", @"label": @"WeChat"},
            @{@"provider": @"app:line", @"label": @"LINE"},
            @{@"provider": @"app:webex", @"label": @"Webex"},
            @{@"provider": @"app:element", @"label": @"Element"},
            @{@"provider": @"app:phone", @"label": @"Phone"},
            @{@"provider": @"site:voice.google.com", @"label": @"Google Voice"},
            @{@"provider": @"site:messenger.com", @"label": @"Messenger"},
            @{@"provider": @"site:web.groupme.com", @"label": @"GroupMe"},
        ];
    });
    return catalog;
}

static JardCall *makeCall(NSString *provider, NSString *application, NSString *label, NSString *extra, BOOL slow) {
    JardCall *call = JardCall.new;
    call.provider = provider;
    call.application = application;
    call.label = label;
    call.identity = extra.length ? [NSString stringWithFormat:@"%@|%@|%@", application, provider, extra] :
        [NSString stringWithFormat:@"%@|%@", application, provider];
    call.slow = slow;
    call.inputActive = YES;
    return call;
}

JardCall *JardMeetingDirectCall(NSString *bundle) {
    NSDictionary *apps = @{
        @"us.zoom.xos": @"Zoom", @"us.zoom.cpthost": @"Zoom",
        @"com.microsoft.teams2": @"Microsoft Teams", @"com.microsoft.teams": @"Microsoft Teams",
        @"com.tinyspeck.slackmacgap": @"Slack", @"com.apple.facetime": @"FaceTime",
        @"com.apple.mobilephone": @"Phone", @"com.apple.avconferenced": @"FaceTime",
        @"com.apple.telephonyutilities.callservicesd": @"FaceTime",
        @"net.whatsapp.whatsapp": @"WhatsApp", @"desktop.whatsapp": @"WhatsApp",
        @"com.hnc.discord": @"Discord", @"com.hnc.discordcanary": @"Discord", @"com.hnc.discordptb": @"Discord",
        @"com.gather.gatherv2": @"Gather", @"com.gather.gather": @"Gather",
        @"org.whispersystems.signal-desktop": @"Signal", @"ru.keepcoder.telegram": @"Telegram",
        @"com.tdesktop.telegram": @"Telegram", @"org.telegram.desktop": @"Telegram",
        @"com.tencent.xinwechat": @"WeChat", @"jp.naver.line.mac": @"LINE",
        @"com.webex.meetingmanager": @"Webex", @"com.cisco.webexmeetingsapp": @"Webex",
        @"cisco-systems.spark": @"Webex", @"im.riot.app": @"Element"
    };
    NSString *canonical = bundle.lowercaseString;
    while (canonical.length) {
        NSString *label = apps[canonical];
        if (label) {
            BOOL slow = [@[@"WhatsApp", @"Gather", @"Signal", @"Telegram", @"WeChat", @"LINE", @"Element"] containsObject:label];
            NSString *provider = [@"app:" stringByAppendingString:label.lowercaseString];
            return makeCall(provider, provider, label, @"", slow);
        }
        NSRange last = [canonical rangeOfString:@"." options:NSBackwardsSearch];
        if (last.location == NSNotFound) break;
        canonical = [canonical substringToIndex:last.location];
    }
    return nil;
}

static BOOL siteMatches(NSString *host, NSString *domain) {
    return [host isEqual:domain] || [host hasSuffix:[@"." stringByAppendingString:domain]];
}

JardCall *JardMeetingBrowserCall(NSURL *url, NSString *application) {
    if (![url.scheme.lowercaseString isEqual:@"https"] || !url.host.length) return nil;
    NSString *host = url.host.lowercaseString;
    NSArray *services = @[
        @[@"meet.google.com", @"Google Meet"], @[@"zoom.us", @"Zoom"], @[@"zoom.com", @"Zoom"], @[@"zoomgov.com", @"Zoom"],
        @[@"teams.microsoft.com", @"Microsoft Teams"], @[@"teams.cloud.microsoft", @"Microsoft Teams"], @[@"teams.live.com", @"Microsoft Teams"],
        @[@"meet.jit.si", @"Jitsi Meet"], @[@"webex.com", @"Webex"], @[@"app.slack.com", @"Slack"], @[@"banshee.app", @"Banshee"],
        @[@"facetime.apple.com", @"FaceTime"], @[@"voice.google.com", @"Google Voice"], @[@"discord.com", @"Discord"],
        @[@"web.whatsapp.com", @"WhatsApp"], @[@"app.gather.town", @"Gather"], @[@"app.v2.gather.town", @"Gather"],
        @[@"messenger.com", @"Messenger"], @[@"web.groupme.com", @"GroupMe"]
    ];
    for (NSArray<NSString *> *service in services) {
        if (!siteMatches(host, service[0])) continue;
        NSString *label = service[1];
        if ([label isEqual:@"Slack"] && ![url.path.lowercaseString containsString:@"/huddle"]) return nil;
        NSString *key = [label isEqual:@"Microsoft Teams"] ? @"app:microsoft teams" : [@"site:" stringByAppendingString:service[0]];
        NSString *callID = @"";
        if ([label isEqual:@"Google Meet"]) {
            for (NSString *component in url.pathComponents) {
                NSRange range = [component rangeOfString:@"^[a-z]{3}-[a-z]{4}-[a-z]{3}$"
                    options:NSRegularExpressionSearch | NSCaseInsensitiveSearch];
                if (range.location != NSNotFound) { callID = component.lowercaseString; break; }
            }
            if (!callID.length) return nil;
        }
        BOOL slow = [@[@"WhatsApp", @"Gather", @"Messenger", @"GroupMe"] containsObject:label];
        return makeCall(key, application, label, callID, slow);
    }
    return nil;
}

JardCall *JardMeetingUnnamedBrowser(NSString *application, NSString *name) {
    JardCall *call = makeCall([@"browser:" stringByAppendingString:application], application, name, @"", YES);
    call.unnamedBrowser = YES;
    return call;
}

static BOOL browser(NSString *bundle) {
    NSArray *browsers = @[@"com.apple.safari", @"com.google.chrome", @"org.mozilla.firefox", @"com.microsoft.edgemac",
        @"com.brave.browser", @"company.thebrowser.browser", @"com.vivaldi.vivaldi", @"com.operasoftware.opera",
        @"net.imput.helium", @"com.ardia.browser", @"app.zen-browser.zen"];
    for (NSString *known in browsers) {
        if ([bundle isEqual:known] || [bundle hasPrefix:[known stringByAppendingString:@"."]]) return YES;
    }
    return NO;
}

static id ax(AXUIElementRef element, CFStringRef name) {
    CFTypeRef value = NULL;
    if (AXUIElementCopyAttributeValue(element, name, &value) != kAXErrorSuccess) return nil;
    return CFBridgingRelease(value);
}

static JardCall *browserCall(pid_t pid, NSString *application) {
    if (!AXIsProcessTrusted()) return nil;
    AXUIElementRef app = AXUIElementCreateApplication(pid);
    AXUIElementSetMessagingTimeout(app, .025);
    id focused = ax(app, kAXFocusedWindowAttribute);
    NSArray *windows = ax(app, kAXWindowsAttribute);
    CFRelease(app);
    NSMutableArray *nodes = NSMutableArray.array;
    if (focused) [nodes addObject:focused];
    if ([windows isKindOfClass:NSArray.class]) {
        for (id window in windows) if (window != focused && nodes.count < 5) [nodes addObject:window];
    }
    NSTimeInterval limit = NSProcessInfo.processInfo.systemUptime + .14;
    for (NSUInteger i = 0; i < nodes.count && i < 72 && NSProcessInfo.processInfo.systemUptime < limit; ++i) {
        id node = nodes[i];
        if (CFGetTypeID((__bridge CFTypeRef)node) != AXUIElementGetTypeID()) continue;
        AXUIElementRef element = (__bridge AXUIElementRef)node;
        id role = ax(element, kAXRoleAttribute);
        if ([role isEqual:@"AXWebArea"]) {
            id raw = ax(element, kAXURLAttribute);
            NSURL *url = [raw isKindOfClass:NSURL.class] ? raw :
                [raw isKindOfClass:NSString.class] ? [NSURL URLWithString:raw] : nil;
            JardCall *call = url ? JardMeetingBrowserCall(url, application) : nil;
            if (call) return call;
        } else {
            NSArray *children = ax(element, kAXChildrenAttribute);
            if ([children isKindOfClass:NSArray.class]) {
                for (id child in children) {
                    if (nodes.count >= 72) break;
                    if (![nodes containsObject:child]) [nodes addObject:child];
                }
            }
        }
    }
    return nil;
}

static OSStatus audioProperty(AudioObjectID object, AudioObjectPropertySelector selector, UInt32 *bytes, void *value) {
    AudioObjectPropertyAddress address = {selector, kAudioObjectPropertyScopeGlobal, kAudioObjectPropertyElementMain};
    return AudioObjectGetPropertyData(object, &address, 0, NULL, bytes, value);
}

static pid_t parentOf(pid_t pid) {
    struct proc_bsdinfo info;
    if (proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof info) != sizeof info) return 0;
    return (pid_t)info.pbi_ppid;
}

NSArray<JardCall *> *JardActiveCalls(BOOL includeOutput) {
    AudioObjectPropertyAddress address = {kAudioHardwarePropertyProcessObjectList, kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain};
    UInt32 bytes = 0;
    AudioObjectID system = kAudioObjectSystemObject;
    if (AudioObjectGetPropertyDataSize(system, &address, 0, NULL, &bytes) != noErr || bytes > 65536) return nil;
    if (!bytes) return @[];
    NSMutableData *objects = [NSMutableData dataWithLength:bytes];
    if (audioProperty(system, kAudioHardwarePropertyProcessObjectList, &bytes, objects.mutableBytes) != noErr) return nil;
    const AudioObjectID *ids = objects.bytes;
    NSMutableDictionary<NSString *, JardCall *> *found = NSMutableDictionary.dictionary;
    NSMutableDictionary<NSNumber *, JardCall *> *browserCalls = NSMutableDictionary.dictionary;
    for (UInt32 i = 0; i < bytes / sizeof *ids; ++i) {
        UInt32 running = 0, size = sizeof running;
        BOOL input = audioProperty(ids[i], kAudioProcessPropertyIsRunningInput, &size, &running) == noErr && running;
        if (!input) {
            running = 0;
            size = sizeof running;
            if (!includeOutput || audioProperty(ids[i], kAudioProcessPropertyIsRunningOutput, &size, &running) != noErr || !running) continue;
        }
        pid_t pid = 0;
        size = sizeof pid;
        if (audioProperty(ids[i], kAudioProcessPropertyPID, &size, &pid) != noErr || pid <= 1 || pid == getpid()) continue;
        CFStringRef idValue = NULL;
        size = sizeof idValue;
        if (audioProperty(ids[i], kAudioProcessPropertyBundleID, &size, &idValue) != noErr) idValue = NULL;
        NSString *reported = CFBridgingRelease(idValue);
        JardCall *call = nil;
        for (pid_t host = pid, depth = 0; host > 1 && depth < 6; ++depth, host = parentOf(host)) {
            NSRunningApplication *app = [NSRunningApplication runningApplicationWithProcessIdentifier:host];
            NSString *bundle = app.bundleIdentifier.lowercaseString;
            call = JardMeetingDirectCall(bundle);
            if (call) break;
            if (!browser(bundle) || app.activationPolicy != NSApplicationActivationPolicyRegular) continue;
            call = browserCalls[@(host)];
            if (!call) {
                call = browserCall(host, bundle) ?: JardMeetingUnnamedBrowser(bundle, app.localizedName ?: @"Browser");
                browserCalls[@(host)] = call;
            }
            break;
        }
        if (!call && reported) call = JardMeetingDirectCall(reported);
        if (call) {
            call.inputActive = input || found[call.identity].inputActive;
            found[call.identity] = call;
        }
    }
    return [found.allValues sortedArrayUsingComparator:^NSComparisonResult(JardCall *a, JardCall *b) {
        return [a.identity compare:b.identity];
    }];
}

@interface JardMeetingPresence ()
@property(nonatomic, strong) NSMutableDictionary<NSString *, NSNumber *> *lastSeen, *start, *absence;
@property(nonatomic) NSTimeInterval lastPoll;
@end

@implementation JardMeetingPresence
- (instancetype)init {
    if (!(self = [super init])) return nil;
    _lastSeen = NSMutableDictionary.dictionary;
    _start = NSMutableDictionary.dictionary;
    _absence = NSMutableDictionary.dictionary;
    return self;
}
- (void)resetObservations {
    [self.start removeAllObjects];
    self.lastPoll = 0;
}
- (void)snooze:(JardCall *)call absence:(NSTimeInterval)seconds now:(NSTimeInterval)now {
    self.absence[call.identity] = @(seconds);
    self.lastSeen[call.identity] = @(now);
}
- (JardCall *)offerFromCalls:(NSArray<JardCall *> *)calls now:(NSTimeInterval)now {
    if (self.lastPoll && now - self.lastPoll > 10) [self.start removeAllObjects];
    self.lastPoll = now;
    for (NSString *key in self.lastSeen.allKeys) {
        if (now - self.lastSeen[key].doubleValue >= (self.absence[key] ?: @2.5).doubleValue) {
            [self.start removeObjectForKey:key];
            [self.lastSeen removeObjectForKey:key];
            [self.absence removeObjectForKey:key];
        }
    }
    JardCall *offer = nil;
    for (JardCall *call in calls) {
        if (!call.inputActive) continue;
        NSString *key = call.identity;
        if (!self.start[key] || now - self.lastSeen[key].doubleValue > 2.5) self.start[key] = @(now);
        self.lastSeen[key] = @(now);
        if (!offer && !self.absence[key] && now - self.start[key].doubleValue >= (call.slow ? 20 : 2)) offer = call;
    }
    return offer;
}
@end
