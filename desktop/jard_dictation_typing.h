#import <Foundation/Foundation.h>
#import <ApplicationServices/ApplicationServices.h>
#import <Carbon/Carbon.h>

typedef NS_ENUM(NSUInteger, JardInsertionStyle) {
    JardInsertPaste,
    JardInsertBurst,
    JardInsertPaced,
};

typedef struct {
    int16_t plain[26];
    int16_t command[26];
} JardShortcutLayout;

JardInsertionStyle JardInsertionForApp(NSString *mode, NSString *bundleIdentifier, NSURL *bundleURL);
CGKeyCode JardPasteKeyCodeForSources(TISInputSourceRef active, TISInputSourceRef asciiCapable);
CGKeyCode JardCurrentPasteKeyCode(void);
JardShortcutLayout JardShortcutLayoutForSources(TISInputSourceRef active, TISInputSourceRef asciiCapable);
JardShortcutLayout JardCurrentShortcutLayout(void);
NSInteger JardShortcutLetterCode(const JardShortcutLayout *layout, unichar letter, BOOL command, NSInteger recorded);
NSDictionary<NSString *, NSDictionary *> *JardTypingLayout(void);
NSArray *JardTypingEvents(NSString *text, NSUInteger *cursor, JardInsertionStyle style, NSDictionary *layout, int64_t marker);
