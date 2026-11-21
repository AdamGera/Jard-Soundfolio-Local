#import "jard_secure_hotkeys.h"
#import <Carbon/Carbon.h>

static const OSType shortcutSignature = 'JrSc';
static const CGEventFlags carbonFlags = kCGEventFlagMaskCommand | kCGEventFlagMaskAlternate |
    kCGEventFlagMaskControl | kCGEventFlagMaskShift;

static BOOL compatible(JardSecureChord chord) {
    return chord.code >= 0 && chord.code <= 127 && !(chord.flags & ~carbonFlags);
}

static UInt32 carbonModifiers(CGEventFlags flags) {
    return (flags & kCGEventFlagMaskCommand ? cmdKey : 0) |
        (flags & kCGEventFlagMaskAlternate ? optionKey : 0) |
        (flags & kCGEventFlagMaskControl ? controlKey : 0) |
        (flags & kCGEventFlagMaskShift ? shiftKey : 0);
}

static BOOL sameKeys(JardSecureChord first, JardSecureChord second) {
    return first.code == second.code && first.flags == second.flags;
}

@interface JardSecureRegistration : NSObject
@property(nonatomic) JardSecureChord chord;
@property(nonatomic) UInt32 identifier;
@property(nonatomic) EventHotKeyRef hotkey;
@property(nonatomic) BOOL held;
@end
@implementation JardSecureRegistration
@end

@interface JardSecureHotkeys ()
@property(nonatomic, copy) JardSecureHotkeyHandler handler;
@property(nonatomic, strong) NSMutableArray<JardSecureRegistration *> *registrations;
@property(nonatomic) EventHandlerRef eventHandler;
@property(nonatomic) UInt32 nextIdentifier;
@end

static OSStatus receiveHotkey(EventHandlerCallRef next, EventRef event, void *context);

@implementation JardSecureHotkeys

- (instancetype)initWithHandler:(JardSecureHotkeyHandler)handler {
    if (!(self = [super init])) return nil;
    _handler = [handler copy];
    _registrations = NSMutableArray.array;
    return self;
}

- (void)update:(const JardSecureChord *)chords count:(NSUInteger)count {
    if (!count && !self.registrations.count) return;
    NSMutableArray<JardSecureRegistration *> *wanted = NSMutableArray.array;
    for (NSUInteger i = 0; i < count; i++) {
        JardSecureChord chord = chords[i];
        if (!compatible(chord)) continue;
        if ([wanted indexOfObjectPassingTest:^BOOL(JardSecureRegistration *item, NSUInteger index, BOOL *stop) {
            return sameKeys(item.chord, chord);
        }] != NSNotFound) continue;
        JardSecureRegistration *item = JardSecureRegistration.new;
        item.chord = chord;
        [wanted addObject:item];
    }
    NSMutableArray<JardSecureRegistration *> *removed = NSMutableArray.array;
    for (JardSecureRegistration *item in self.registrations.copy) {
        NSUInteger index = [wanted indexOfObjectPassingTest:^BOOL(JardSecureRegistration *request, NSUInteger i, BOOL *stop) {
            return item.chord.action == request.chord.action && sameKeys(item.chord, request.chord);
        }];
        if (index != NSNotFound) {
            [wanted removeObjectAtIndex:index];
        } else {
            [self.registrations removeObject:item];
            UnregisterEventHotKey(item.hotkey);
            if (item.held) [removed addObject:item];
        }
    }
    if (wanted.count && !self.eventHandler) {
        const EventTypeSpec types[] = {{kEventClassKeyboard, kEventHotKeyPressed}, {kEventClassKeyboard, kEventHotKeyReleased}};
        EventHandlerRef handler = NULL;
        if (InstallEventHandler(GetApplicationEventTarget(), receiveHotkey, 2, types, (__bridge void *)self, &handler) == noErr)
            self.eventHandler = handler;
    }
    if (self.eventHandler) {
        for (JardSecureRegistration *item in wanted) {
            EventHotKeyID identity = {shortcutSignature, ++_nextIdentifier};
            EventHotKeyRef hotkey = NULL;
            if (RegisterEventHotKey((UInt32)item.chord.code, carbonModifiers(item.chord.flags), identity,
                    GetApplicationEventTarget(), kEventHotKeyExclusive, &hotkey) != noErr || !hotkey) continue;
            item.identifier = identity.id;
            item.hotkey = hotkey;
            [self.registrations addObject:item];
        }
        if (!self.registrations.count) {
            RemoveEventHandler(self.eventHandler);
            self.eventHandler = NULL;
        }
    }
    for (JardSecureRegistration *item in removed) self.handler(item.chord, JardSecureCancelled);
}

- (OSStatus)receive:(EventHotKeyID)identity down:(BOOL)down {
    if (identity.signature != shortcutSignature) return eventNotHandledErr;
    for (JardSecureRegistration *item in self.registrations) {
        if (item.identifier != identity.id) continue;
        if (item.held != down) {
            item.held = down;
            self.handler(item.chord, down ? JardSecurePressed : JardSecureReleased);
        }
        return noErr;
    }
    return eventNotHandledErr;
}

- (void)dealloc {
    for (JardSecureRegistration *item in _registrations) UnregisterEventHotKey(item.hotkey);
    if (_eventHandler) RemoveEventHandler(_eventHandler);
}

@end

static OSStatus receiveHotkey(EventHandlerCallRef next, EventRef event, void *context) {
    @autoreleasepool {
        EventHotKeyID identity;
        if (GetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID, NULL, sizeof(identity), NULL, &identity) != noErr)
            return eventNotHandledErr;
        JardSecureHotkeys *owner = (__bridge JardSecureHotkeys *)context;
        return [owner receive:identity down:GetEventKind(event) == kEventHotKeyPressed];
    }
}
