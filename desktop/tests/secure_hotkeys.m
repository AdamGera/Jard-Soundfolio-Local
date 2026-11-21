#import "../jard_secure_hotkeys.h"
#import <AppKit/AppKit.h>
#import <Carbon/Carbon.h>
#import <assert.h>

static void event(UInt32 identifier, BOOL down, OSType signature) {
    EventRef value = NULL;
    assert(CreateEvent(NULL, kEventClassKeyboard, down ? kEventHotKeyPressed : kEventHotKeyReleased,
        GetCurrentEventTime(), kEventAttributeNone, &value) == noErr);
    EventHotKeyID hotkey = {signature, identifier};
    assert(SetEventParameter(value, kEventParamDirectObject, typeEventHotKeyID, sizeof(hotkey), &hotkey) == noErr);
    SendEventToEventTarget(value, GetApplicationEventTarget());
    ReleaseEvent(value);
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        NSMutableArray<NSNumber *> *received = NSMutableArray.array;
        JardSecureHotkeys *hotkeys = [[JardSecureHotkeys alloc] initWithHandler:^(JardSecureChord chord, JardSecureEdge edge) {
            [received addObject:@(3 * chord.action + edge)];
        }];
        CGEventFlags flags = kCGEventFlagMaskControl | kCGEventFlagMaskAlternate | kCGEventFlagMaskCommand;
        JardSecureChord choices[] = {
            {JardSecureAnnotate, kVK_F20, flags},
            {JardSecurePasteLast, kVK_F20, flags}, // Shadowed by the first eligible action.
            {JardSecurePasteLast, kVK_F19, flags | kCGEventFlagMaskSecondaryFn},
            {JardSecurePasteLast, 140, flags},
            {JardSecureMeetingPaste, kVK_F18, flags},
        };
        const OSType identity = 'JrSc';
        BOOL secureFixture = argc > 1 && [@(argv[1]) isEqual:@"--secure"];
        BOOL enabledByFixture = secureFixture && !IsSecureEventInputEnabled();
        if (enabledByFixture) assert(EnableSecureEventInput() == noErr);
        if (secureFixture) assert(IsSecureEventInputEnabled());
        @try {
            [hotkeys update:choices count:sizeof(choices) / sizeof(choices[0])];
            event(1, YES, identity);
            event(1, NO, identity);
            assert(([received isEqual:@[@(JardSecurePressed), @(JardSecureReleased)]]));
        } @finally {
            if (enabledByFixture) assert(DisableSecureEventInput() == noErr);
        }
        if (enabledByFixture) assert(!IsSecureEventInputEnabled());
        [received removeAllObjects];
        event(1, YES, 'Else');
        event(1, NO, identity);
        assert(received.count == 0);
        event(1, YES, identity);
        event(1, YES, identity); // Carbon repeat and duplicate presses stay one chord.
        event(2, YES, identity);
        event(2, NO, identity);
        assert(([received isEqual:@[@(JardSecurePressed), @(3 * JardSecureMeetingPaste + JardSecurePressed),
            @(3 * JardSecureMeetingPaste + JardSecureReleased)]]));
        [hotkeys update:choices count:sizeof(choices) / sizeof(choices[0])];
        assert(received.count == 3);  // Polling with no changes keeps the hold.
        [hotkeys update:NULL count:0];
        assert(received.count == 4 && received.lastObject.unsignedIntValue == JardSecureCancelled);
        event(1, NO, identity);
        assert(received.count == 4);

        [hotkeys update:choices count:1];
        event(1, YES, identity); // Stale event for the first registration.
        assert(received.count == 4);
        event(3, YES, identity);
        event(3, NO, identity);
        assert(received.count == 6 && received.lastObject.unsignedIntValue == JardSecureReleased);
        [hotkeys update:NULL count:0];
        assert(received.count == 6);
        puts("Carbon registered and released real hotkeys, preserved unchanged holds, ignored duplicates and stale events, and cancelled a disappearing hold.");
    }
}
