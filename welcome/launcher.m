// Rebuild from the repository root with: bash welcome/build-launcher.sh
#import <Cocoa/Cocoa.h>

@interface Launcher : NSObject <NSApplicationDelegate>
@end

@implementation Launcher
- (NSURL *)folder {
    return NSBundle.mainBundle.bundleURL.URLByDeletingLastPathComponent;
}

- (void)showError:(NSString *)message {
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"Jaro Launcher";
    alert.informativeText = message;
    [alert runModal];
}

- (BOOL)applicationOpenUntitledFile:(NSApplication *)sender {
    NSURL *page = [self.folder URLByAppendingPathComponent:@"README.html"];
    if (![NSWorkspace.sharedWorkspace openURL:page]) {
        [self showError:@"Keep Jaro Launcher in the same folder as README.html."];
    }
    [NSApp terminate:nil];
    return YES;
}

- (void)application:(NSApplication *)sender openURLs:(NSArray<NSURL *> *)urls {
    NSDictionary *commands = @{@"setup": @"Setup Jaro.command",
                               @"start": @"Start Jaro.command",
                               @"quit": @"Quit Jaro.command"};
    NSString *page = [[self.folder URLByAppendingPathComponent:@"README.html"] URLByResolvingSymlinksInPath].path;
    for (NSURL *url in urls) {
        NSURLComponents *parts = [NSURLComponents componentsWithURL:url resolvingAgainstBaseURL:NO];
        NSString *command = parts.host ? commands[parts.host] : nil;
        BOOL knownScheme = [@[@"jaro", @"jard-soundfolio-local", @"jard-audio"] containsObject:parts.scheme ?: @""];
        if (!knownScheme || !command) continue;
        NSURLQueryItem *source = parts.queryItems.firstObject;
        // A link from a different checkout must never run this checkout's commands.
        BOOL fromPage = [source.name isEqualToString:@"page"] &&
            [source.value.stringByResolvingSymlinksInPath.precomposedStringWithCanonicalMapping
                isEqualToString:page.precomposedStringWithCanonicalMapping];
        if (!fromPage) {
            [self showError:@"Open Jaro Launcher in the same folder as your welcome page."];
            break;
        }
        NSURL *file = [self.folder URLByAppendingPathComponent:command];
        if (![NSFileManager.defaultManager isExecutableFileAtPath:file.path]) {
            [self showError:[NSString stringWithFormat:@"Could not run %@ in the welcome page’s folder.", command]];
            break;
        }
        NSTask *task = [NSTask new];
        task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/open"];
        task.arguments = @[@"-a", @"Terminal", file.path];
        if ([task launchAndReturnError:NULL]) {
            [task waitUntilExit];
            if (task.terminationStatus == 0) continue;
        }
        [self showError:[NSString stringWithFormat:@"Terminal could not open %@.", command]];
    }
    [NSApp terminate:nil];
}
@end

int main(void) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        Launcher *delegate = [Launcher new];
        app.delegate = delegate;
        [app run];
    }
    return 0;
}
