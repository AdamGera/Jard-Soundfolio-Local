#import "jard_login_item.h"
#import <ServiceManagement/ServiceManagement.h>

static NSString *loginState(SMAppServiceStatus status) {
    switch (status) {
        case SMAppServiceStatusEnabled: return @"enabled";
        case SMAppServiceStatusRequiresApproval: return @"approval";
        case SMAppServiceStatusNotRegistered:
        case SMAppServiceStatusNotFound: return @"disabled";
    }
    return @"unknown";
}

@interface JardLoginRequest : NSObject
@property(nonatomic, copy) NSString *action;
@property(nonatomic) BOOL enabled;
@property(nonatomic, copy) JardDictationReply reply;
@end

@implementation JardLoginRequest
@end

@interface JardLoginItem ()
@property(nonatomic, strong) dispatch_queue_t queue;
@property(nonatomic) BOOL active;
@property(nonatomic, strong) JardLoginRequest *pending;
@property(nonatomic, strong) NSMutableArray<JardDictationReply> *readers;
@end

@implementation JardLoginItem

- (instancetype)init {
    if (!(self = [super init])) return nil;
    dispatch_queue_attr_t attributes = dispatch_queue_attr_make_with_autorelease_frequency(DISPATCH_QUEUE_SERIAL,
        DISPATCH_AUTORELEASE_FREQUENCY_WORK_ITEM);
    _queue = dispatch_queue_create("local.jardaudio.login-item", attributes);
    _readers = NSMutableArray.array;
    return self;
}

// The serial worker is the only caller of ServiceManagement. Its answer stays
// authoritative even if a write reports an error after changing the setting.
- (NSDictionary *)performLoginAction:(NSString *)action enabled:(BOOL)enabled {
    SMAppService *service = SMAppService.mainAppService;
    NSError *error = nil;
    NSString *message = nil;
    if ([action isEqual:@"openSettings"]) [SMAppService openSystemSettingsLoginItems];
    SMAppServiceStatus status = service.status;
    if ([action isEqual:@"set"] && ![loginState(status) isEqual:@"unknown"]) {
        BOOL disabled = [loginState(status) isEqual:@"disabled"];
        BOOL needsChange = enabled ? disabled : !disabled;
        if (needsChange) {
            BOOL changed = enabled ? [service registerAndReturnError:&error] : [service unregisterAndReturnError:&error];
            if (!changed) message = error.localizedDescription ?: @"macOS could not change Launch at Login.";
            status = service.status;
        }
    }
    NSString *state = loginState(status);
    if ([state isEqual:@"unknown"] && !message) message = @"macOS could not read Launch at Login.";
    return message ? @{@"state": state, @"message": message} : @{@"state": state};
}

- (void)start:(JardLoginRequest *)request {
    self.active = YES;
    dispatch_async(self.queue, ^{
        NSDictionary *result = [self performLoginAction:request.action enabled:request.enabled];
        dispatch_async(dispatch_get_main_queue(), ^{
            JardLoginRequest *next = self.pending;
            self.pending = nil;
            self.active = NO;
            NSArray<JardDictationReply> *readers = nil;
            if (next) [self start:next];
            else {
                readers = self.readers.copy;
                [self.readers removeAllObjects];
            }
            if (request.reply) request.reply(result, nil);
            NSDictionary *readStatus = [result[@"state"] isEqual:@"unknown"] ? result : @{@"state": result[@"state"]};
            for (JardDictationReply reader in readers) reader(readStatus, nil);
        });
    });
}

- (void)request:(NSDictionary *)parameters reply:(JardDictationReply)reply {
    NSString *action = parameters[@"action"];
    id value = parameters[@"enabled"];
    if (![@[@"status", @"set", @"openSettings"] containsObject:action ?: NSNull.null] ||
        ([action isEqual:@"set"] && value != (__bridge id)kCFBooleanTrue && value != (__bridge id)kCFBooleanFalse)) {
        reply(nil, @"Invalid Launch at Login request.");
        return;
    }
    JardLoginRequest *request = JardLoginRequest.new;
    request.action = action;
    if ([action isEqual:@"status"]) {
        [self.readers addObject:[reply copy]];
        if (!self.active) [self start:request];
        return;
    }
    request.enabled = [action isEqual:@"set"] && [value boolValue];
    request.reply = reply;
    if (!self.active) [self start:request];
    else {
        JardLoginRequest *replaced = self.pending;
        self.pending = request;
        if (replaced) replaced.reply(nil, @"A newer Launch at Login request replaced this change.");
    }
}

@end
