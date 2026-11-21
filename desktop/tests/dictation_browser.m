#import "../jard_dictation_browser.h"
#import <ApplicationServices/ApplicationServices.h>
#import <objc/runtime.h>
#import <assert.h>

@interface FixtureBrowser : NSObject
@property(nonatomic, copy) NSString *bundleIdentifier;
@property(nonatomic) pid_t processIdentifier;
@property(nonatomic, getter=isTerminated) BOOL terminated;
@end

@implementation FixtureBrowser
@end

static OSType expectedTab, expectedURL;
static BOOL denied;
static NSUInteger requests;
static NSUInteger permissionRequests;
static NSString *fixtureAddress;

OSStatus AEDeterminePermissionToAutomateTarget(const AEAddressDesc *target, AEEventClass eventClass, AEEventID eventID,
                                                Boolean askUserIfNeeded) {
    NSAppleEventDescriptor *expected = [NSAppleEventDescriptor descriptorWithProcessIdentifier:12345];
    assert(target->descriptorType == expected.descriptorType);
    assert(eventClass == kAECoreSuite && eventID == kAEGetData && askUserIfNeeded);
    permissionRequests++;
    return denied ? errAEEventNotPermitted : noErr;
}

static NSAppleEventDescriptor *replyToTabRead(NSAppleEventDescriptor *event, SEL action,
        NSAppleEventSendOptions options, NSTimeInterval timeout, NSError **error) {
    requests++;
    assert(timeout == .25);
    assert(options == (NSAppleEventSendWaitForReply | NSAppleEventSendNeverInteract | kAEDoNotPromptForUserConsent));
    assert([event attributeDescriptorForKeyword:keyEventClassAttr].typeCodeValue == kAECoreSuite);
    assert([event attributeDescriptorForKeyword:keyEventIDAttr].typeCodeValue == kAEGetData);
    NSAppleEventDescriptor *target = [event attributeDescriptorForKeyword:keyAddressAttr];
    NSAppleEventDescriptor *expected = [NSAppleEventDescriptor descriptorWithProcessIdentifier:12345];
    assert(target.descriptorType == expected.descriptorType && [target.data isEqual:expected.data]);

    NSAppleEventDescriptor *url = [[event paramDescriptorForKeyword:keyDirectObject] coerceToDescriptorType:typeAERecord];
    NSAppleEventDescriptor *tab = [[url descriptorForKeyword:keyAEContainer] coerceToDescriptorType:typeAERecord];
    NSAppleEventDescriptor *window = [[tab descriptorForKeyword:keyAEContainer] coerceToDescriptorType:typeAERecord];
    assert([url descriptorForKeyword:keyAEKeyData].typeCodeValue == expectedURL);
    assert([tab descriptorForKeyword:keyAEKeyData].typeCodeValue == expectedTab);
    assert([window descriptorForKeyword:keyAEDesiredClass].typeCodeValue == cWindow);
    assert([window descriptorForKeyword:keyAEKeyForm].enumCodeValue == formAbsolutePosition);
    assert([window descriptorForKeyword:keyAEKeyData].int32Value == 1);

    NSAppleEventDescriptor *reply = NSAppleEventDescriptor.recordDescriptor;
    if (denied) [reply setDescriptor:[NSAppleEventDescriptor descriptorWithInt32:errAEEventNotPermitted] forKeyword:keyErrorNumber];
    else [reply setDescriptor:[NSAppleEventDescriptor descriptorWithString:fixtureAddress] forKeyword:keyDirectObject];
    return reply;
}

int main(void) {
    @autoreleasepool {
        Method send = class_getInstanceMethod(NSAppleEventDescriptor.class, @selector(sendEventWithOptions:timeout:error:));
        IMP original = method_setImplementation(send, (IMP)replyToTabRead);
        FixtureBrowser *fixture = FixtureBrowser.new;
        fixture.processIdentifier = 12345;
        NSRunningApplication *browser = (NSRunningApplication *)fixture;
        fixtureAddress = @"https://person:token@Docs.Example:443/private?q=keep-local#section";
        fixture.bundleIdentifier = @"com.apple.SafariTechnologyPreview";
        expectedTab = 'cTab';
        expectedURL = 'pURL';
        NSURL *address = JardBrowserTabURL(browser, .25);
        assert([address.host.lowercaseString isEqual:@"docs.example"]);
        assert([address.path isEqual:@"/private"] && [address.query isEqual:@"q=keep-local"]);
        assert([address.fragment isEqual:@"section"] && !address.user && !address.password);
        fixture.bundleIdentifier = @"com.google.Chrome";
        expectedTab = 'acTa';
        expectedURL = 'URL ';
        assert([JardBrowserTabURL(browser, .25).host.lowercaseString isEqual:@"docs.example"]);
        fixtureAddress = @"file:///tmp/local.html";
        assert(JardBrowserTabURL(browser, .25) == nil);
        assert(permissionRequests == 0);
        assert(JardRequestBrowserTabAccess(browser));
        denied = YES;
        assert(JardBrowserTabURL(browser, .25) == nil);
        assert(!JardRequestBrowserTabAccess(browser));
        fixture.terminated = YES;
        assert(JardBrowserTabURL(browser, .25) == nil);
        fixture.terminated = NO;
        assert(JardBrowserTabURL(browser, 0) == nil);
        fixture.bundleIdentifier = @"org.mozilla.firefox";
        assert(!JardSupportsBrowserTabAccess(fixture.bundleIdentifier));
        assert(JardBrowserTabURL(browser, .25) == nil);
        assert(!JardRequestBrowserTabAccess(browser));
        assert(requests == 4 && permissionRequests == 2);
        method_setImplementation(send, original);
        puts("Safari and Chromium tab requests were PID-addressed, read-only and nonprompting. HTTP and HTTPS paths, queries and fragments were preserved without credentials.");
    }
    return 0;
}
