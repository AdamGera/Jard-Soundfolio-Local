#include <assert.h>
#include "../dictation_shortcut.h"

static void testModifierHoldOrDoubleTap(void) {
    JardShortcut shortcut = {.mode = JardHoldOrDoubleTap};
    assert(jardShortcutDown(&shortcut, 1.00, true) == JardArm);
    assert(jardShortcutUp(&shortcut, 1.10) == JardNoAction);
    assert(jardShortcutDown(&shortcut, 1.30, true) == JardArm);
    assert(shortcut.phase == JardArmed);
    assert(jardShortcutUp(&shortcut, 1.39) == JardStart);
    assert(shortcut.phase == JardLocked);
    assert(!jardShortcutTakesCancel(&shortcut, 1.80));
    assert(jardShortcutDown(&shortcut, 1.81, true) == JardFinish);
    assert(jardShortcutCancel(&shortcut) == JardCancel);

    assert(jardShortcutDown(&shortcut, 2.00, true) == JardArm);
    assert(jardShortcutArm(&shortcut) == JardStart);
    assert(jardShortcutUp(&shortcut, 2.29) == JardDiscard);
    assert(jardShortcutDown(&shortcut, 2.49, true) == JardArm);
    assert(jardShortcutArm(&shortcut) == JardStart);
    assert(jardShortcutUp(&shortcut, 2.80) == JardFinish);
    assert(jardShortcutCancel(&shortcut) == JardCancel);

    assert(jardShortcutDown(&shortcut, 3.00, true) == JardArm);
    assert(jardShortcutChord(&shortcut, 3.10, true, true) == JardNoAction);
    assert(jardShortcutArm(&shortcut) == JardNoAction);
    assert(jardShortcutUp(&shortcut, 3.20) == JardNoAction);
    assert(shortcut.lastTap == 0);
}

static void testKeyedHold(void) {
    JardShortcut shortcut = {.mode = JardHoldOrDoubleTap};
    assert(jardShortcutDown(&shortcut, 4.00, false) == JardStart);
    assert(jardShortcutUp(&shortcut, 4.10) == JardDiscard);
    assert(jardShortcutDown(&shortcut, 4.20, false) == JardStart);
    assert(shortcut.phase == JardHeld);
    assert(jardShortcutUp(&shortcut, 4.39) == JardLock);
    assert(jardShortcutCancel(&shortcut) == JardCancel);

    shortcut.mode = JardHold;
    assert(jardShortcutDown(&shortcut, 5.00, false) == JardStart);
    assert(jardShortcutUp(&shortcut, 5.299) == JardDiscard);
    assert(shortcut.lastTap == 0 && shortcut.phase == JardIdle);
    assert(jardShortcutDown(&shortcut, 5.50, false) == JardStart);
    assert(!jardShortcutTakesCancel(&shortcut, 5.799));
    assert(jardShortcutTakesCancel(&shortcut, 5.801));
    assert(jardShortcutUp(&shortcut, 5.801) == JardFinish);
    assert(!jardShortcutTakesCancel(&shortcut, 5.90));
}

static void testTap(void) {
    JardShortcut shortcut = {.mode = JardToggle};
    assert(jardShortcutDown(&shortcut, 6.00, true) == JardArm);
    assert(jardShortcutUp(&shortcut, 6.10) == JardStart);
    assert(shortcut.phase == JardLocked);
    assert(jardShortcutTakesCancel(&shortcut, 6.10));
    assert(jardShortcutDown(&shortcut, 6.20, true) == JardFinish);
    assert(jardShortcutCancel(&shortcut) == JardCancel);
    assert(jardShortcutDown(&shortcut, 6.30, false) == JardStart);
    assert(jardShortcutUp(&shortcut, 6.40) == JardNoAction);
    assert(jardShortcutDown(&shortcut, 6.50, false) == JardFinish);
}

static void testDoubleTapOnly(void) {
    JardShortcut shortcut = {.mode = JardDoubleTap};
    assert(jardShortcutDown(&shortcut, 7.00, true) == JardArm);
    assert(jardShortcutArm(&shortcut) == JardNoAction);
    assert(jardShortcutUp(&shortcut, 7.40) == JardNoAction);
    assert(shortcut.lastTap == 0);

    assert(jardShortcutDown(&shortcut, 8.00, false) == JardArm);
    assert(jardShortcutUp(&shortcut, 8.10) == JardNoAction);
    assert(jardShortcutDown(&shortcut, 8.20, false) == JardArm);
    assert(jardShortcutUp(&shortcut, 8.401) == JardNoAction);
    assert(shortcut.lastTap == 0 && shortcut.phase == JardIdle);

    assert(jardShortcutDown(&shortcut, 9.00, false) == JardArm);
    assert(jardShortcutUp(&shortcut, 9.10) == JardNoAction);
    assert(jardShortcutDown(&shortcut, 9.20, false) == JardArm);
    assert(shortcut.phase == JardArmed);
    assert(jardShortcutUp(&shortcut, 9.39) == JardStart);
    assert(shortcut.phase == JardLocked);
    assert(jardShortcutDown(&shortcut, 9.50, false) == JardFinish);
}

int main(void) {
    testModifierHoldOrDoubleTap();
    testKeyedHold();
    testTap();
    testDoubleTapOnly();
}
