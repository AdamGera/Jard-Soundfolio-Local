#import <Foundation/Foundation.h>

BOOL JardIsAnnotationTerminal(NSString *bundleID);
NSDictionary *JardAnnotationTerminalContext(pid_t terminalPID, NSString *title, NSString *directoryHint);
