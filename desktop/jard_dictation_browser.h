#import <AppKit/AppKit.h>

BOOL JardSupportsBrowserTabAccess(NSString *bundleIdentifier);
NSURL *JardBrowserTabURL(NSRunningApplication *browser, NSTimeInterval timeout);
NSURL *JardWebContextURL(id value);
BOOL JardRequestBrowserTabAccess(NSRunningApplication *browser);
