#import "../jard_login_item.h"
#import <assert.h>

@interface JardLoginItem (TestAccess)
- (NSDictionary *)performLoginAction:(NSString *)action enabled:(BOOL)enabled;
@end

@interface LoginFixture : JardLoginItem
@property(nonatomic, strong) dispatch_semaphore_t gate;
@property(nonatomic, copy) NSString *state;
@property(nonatomic, strong) NSMutableArray<NSString *> *calls;
@end

@implementation LoginFixture

- (NSDictionary *)performLoginAction:(NSString *)action enabled:(BOOL)enabled {
    assert(!NSThread.isMainThread);
    @synchronized (self) {
        [self.calls addObject:[action isEqual:@"set"] ? (enabled ? @"enable" : @"disable") : action];
    }
    assert(dispatch_semaphore_wait(self.gate, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC)) == 0);
    if (![action isEqual:@"set"]) return @{@"state": self.state};
    if (enabled) {
        self.state = @"enabled";
        return @{@"state": self.state};
    }
    self.state = @"approval";
    return @{@"state": self.state, @"message": @"Fixture registration error"};
}

@end

static void waitUntil(BOOL (^condition)(void)) {
    NSDate *deadline = [NSDate dateWithTimeIntervalSinceNow:5];
    while (!condition() && deadline.timeIntervalSinceNow > 0)
        [NSRunLoop.mainRunLoop runUntilDate:[NSDate dateWithTimeIntervalSinceNow:.005]];
    assert(condition());
}

static void waitForCall(LoginFixture *fixture, NSString *expected, NSUInteger count) {
    waitUntil(^BOOL {
        @synchronized (fixture) { return fixture.calls.count >= count; }
    });
    @synchronized (fixture) { assert(fixture.calls.count == count && [fixture.calls.lastObject isEqual:expected]); }
}

int main(void) {
    @autoreleasepool {
        LoginFixture *fixture = LoginFixture.new;
        fixture.gate = dispatch_semaphore_create(0);
        fixture.state = @"disabled";
        fixture.calls = NSMutableArray.array;
        __block NSUInteger reads = 0, writes = 0;
        JardDictationReply enabledReader = ^(NSDictionary *result, NSString *error) {
            assert(NSThread.isMainThread && !error && [result[@"state"] isEqual:@"enabled"]);
            reads++;
        };
        [fixture request:@{@"action": @"status"} reply:enabledReader];
        waitForCall(fixture, @"status", 1);
        [fixture request:@{@"action": @"set", @"enabled": @NO} reply:^(id result, NSString *error) {
            assert(!result && [error containsString:@"newer"]);
            writes++;
        }];
        [fixture request:@{@"action": @"set", @"enabled": @YES} reply:^(NSDictionary *result, NSString *error) {
            assert(!error && [result[@"state"] isEqual:@"enabled"]);
            writes++;
        }];
        for (NSUInteger i = 0; i < 4; i++) [fixture request:@{@"action": @"status"} reply:enabledReader];
        assert(writes == 1 && reads == 0);
        dispatch_semaphore_signal(fixture.gate);
        waitForCall(fixture, @"enable", 2);
        assert(reads == 0);
        dispatch_semaphore_signal(fixture.gate);
        waitUntil(^BOOL { return reads == 5 && writes == 2; });

        [fixture request:@{@"action": @"set", @"enabled": @NO} reply:^(NSDictionary *result, NSString *error) {
            assert(!error && [result[@"state"] isEqual:@"approval"] && [result[@"message"] hasPrefix:@"Fixture"]);
            writes++;
        }];
        waitForCall(fixture, @"disable", 3);
        [fixture request:@{@"action": @"status"} reply:^(NSDictionary *result, NSString *error) {
            assert(!error && [result[@"state"] isEqual:@"approval"] && !result[@"message"]);
            reads++;
        }];
        dispatch_semaphore_signal(fixture.gate);
        waitUntil(^BOOL { return reads == 6 && writes == 3; });
        [fixture request:@{@"action": @"openSettings"} reply:^(NSDictionary *result, NSString *error) {
            assert(!error && [result[@"state"] isEqual:@"approval"]);
            writes++;
        }];
        waitForCall(fixture, @"openSettings", 4);
        dispatch_semaphore_signal(fixture.gate);
        waitUntil(^BOOL { return writes == 4; });
        [fixture request:@{@"action": @"set", @"enabled": @1} reply:^(id result, NSString *error) {
            assert(!result && [error containsString:@"Invalid"]);
        }];
        puts("Login Items ran off the main thread, coalesced reads, prioritized the latest change, and recovered the macOS state after an error.");
    }
}
