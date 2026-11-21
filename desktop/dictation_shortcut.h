#pragma once
#include <stdbool.h>

typedef enum { JardHold, JardHoldOrDoubleTap, JardToggle, JardDoubleTap } JardShortcutMode;
typedef enum { JardIdle, JardArmed, JardHeld, JardLocked, JardFinishing } JardShortcutPhase;
typedef enum { JardNoAction, JardArm, JardStart, JardLock, JardFinish, JardDiscard, JardCancel } JardShortcutAction;

typedef struct {
    JardShortcutMode mode;
    JardShortcutPhase phase;
    double lastTap;
    double pressedAt;
    double firstRelease;
    bool bareModifier;
} JardShortcut;

static const double JardHoldThreshold = 0.3;
static const double JardDoubleTapWindow = 0.3;

// A bare modifier only becomes a hold when it survives the intent delay.
// Releasing early lets the key act as an ordinary modifier or the first tap.
static inline JardShortcutAction jardShortcutDown(JardShortcut *shortcut, double now, bool bareModifier) {
    switch (shortcut->phase) {
        case JardLocked:
            shortcut->phase = JardFinishing;
            return JardFinish;
        case JardIdle: {
            shortcut->pressedAt = now;
            shortcut->bareModifier = bareModifier;
            bool canLock = shortcut->mode == JardHoldOrDoubleTap || shortcut->mode == JardDoubleTap;
            bool secondPress = canLock && shortcut->lastTap > 0 && now >= shortcut->lastTap &&
                now < shortcut->lastTap + JardDoubleTapWindow;
            shortcut->firstRelease = secondPress ? shortcut->lastTap : 0;
            shortcut->lastTap = 0;
            bool waitForIntent = bareModifier || shortcut->mode == JardDoubleTap;
            shortcut->phase = waitForIntent ? JardArmed : shortcut->mode == JardToggle ? JardLocked : JardHeld;
            return shortcut->phase == JardArmed ? JardArm : JardStart;
        }
        default:
            return JardNoAction;
    }
}

static inline JardShortcutAction jardShortcutArm(JardShortcut *shortcut) {
    if (shortcut->phase != JardArmed || shortcut->mode == JardDoubleTap) return JardNoAction;
    shortcut->phase = shortcut->mode == JardToggle ? JardLocked : JardHeld;
    return JardStart;
}

static inline JardShortcutAction jardShortcutUp(JardShortcut *shortcut, double now) {
    bool opened = shortcut->phase == JardHeld;
    if ((!opened && shortcut->phase != JardArmed) || now < shortcut->pressedAt) return JardNoAction;
    bool lock = shortcut->firstRelease > 0 && now < shortcut->firstRelease + JardDoubleTapWindow;
    if (shortcut->mode == JardToggle || lock) {
        shortcut->phase = JardLocked;
        return opened ? JardLock : JardStart;
    }
    bool held = now >= shortcut->pressedAt + JardHoldThreshold;
    if (opened && held) {
        shortcut->phase = JardFinishing;
        return JardFinish;
    }
    bool firstTap = (shortcut->mode == JardHoldOrDoubleTap && !held) ||
        (shortcut->mode == JardDoubleTap && !shortcut->firstRelease && (!held || !shortcut->bareModifier));
    shortcut->phase = JardIdle;
    shortcut->lastTap = firstTap ? now : 0;
    return opened ? JardDiscard : JardNoAction;
}

static inline JardShortcutAction jardShortcutCancel(JardShortcut *shortcut) {
    bool recording = shortcut->phase == JardHeld || shortcut->phase == JardLocked || shortcut->phase == JardFinishing;
    shortcut->phase = JardIdle;
    shortcut->lastTap = 0;
    shortcut->firstRelease = 0;
    return recording ? JardCancel : JardNoAction;
}

static inline bool jardShortcutTakesCancel(const JardShortcut *shortcut, double now) {
    if (shortcut->mode == JardToggle) return shortcut->phase == JardHeld || shortcut->phase == JardLocked;
    return shortcut->phase == JardHeld && now >= shortcut->pressedAt + JardHoldThreshold;
}

static inline JardShortcutAction jardShortcutChord(JardShortcut *shortcut, double now, bool triggerDown, bool earlyOnly) {
    if (now >= shortcut->lastTap) shortcut->lastTap = 0;
    if (!triggerDown || now < shortcut->pressedAt) return JardNoAction;
    bool recording = shortcut->phase == JardArmed || shortcut->phase == JardHeld ||
        (shortcut->phase == JardLocked && shortcut->mode == JardToggle);
    bool early = now < shortcut->pressedAt + JardHoldThreshold;
    if (!recording || (!early && (earlyOnly || shortcut->phase != JardHeld))) return JardNoAction;
    return jardShortcutCancel(shortcut) == JardCancel ? JardDiscard : JardNoAction;
}
