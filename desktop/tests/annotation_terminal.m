#import <Foundation/Foundation.h>
#import "../jard_annotation_terminal.h"
#import <util.h>
#import <signal.h>
#import <sys/wait.h>
#import <poll.h>
#import <mach-o/dyld.h>
#include <assert.h>
#include <stdio.h>
#include <unistd.h>

static pid_t startPane(NSString *executable, NSString *folder) {
    const char *path = executable.fileSystemRepresentation, *directory = folder.fileSystemRepresentation;
    int master;
    pid_t pid = forkpty(&master, NULL, NULL, NULL);
    assert(pid >= 0);
    if (!pid) {
        if (chdir(directory)) _exit(2);
        char *argv[] = {(char *)path, "--session=ses_disposable", "--pane-fixture", NULL};
        execv(path, argv);
        _exit(3);
    }
    struct pollfd readReady = {.fd = master, .events = POLLIN};
    assert(poll(&readReady, 1, 3000) > 0);
    char ready[12] = {0};
    assert(read(master, ready, sizeof(ready)) > 0 && strstr(ready, "ready"));
    // Keep the controlling terminal until the foreground sample is done.
    static int masters[2];
    static NSUInteger count;
    assert(count < 2);
    masters[count++] = master;
    return pid;
}

int main(int argc, char **argv) {
    if (argc == 3 && !strcmp(argv[2], "--pane-fixture")) {
        write(STDOUT_FILENO, "ready", 5);
        for (;;) pause();
    }
    @autoreleasepool {
        char path[4096];
        uint32_t size = sizeof(path);
        assert(_NSGetExecutablePath(path, &size) == 0);
        NSString *source = [NSString stringWithUTF8String:path];
        NSString *scratch = [NSTemporaryDirectory() stringByAppendingPathComponent:NSUUID.UUID.UUIDString];
        NSString *project = [scratch stringByAppendingPathComponent:@"team"];
        NSString *inside = [project stringByAppendingPathComponent:@"child"];
        NSString *elsewhere = [scratch stringByAppendingPathComponent:@"other"];
        NSString *executable = [scratch stringByAppendingPathComponent:@"opencode"];
        NSFileManager *files = NSFileManager.defaultManager;
        assert([files createDirectoryAtPath:inside withIntermediateDirectories:YES attributes:nil error:nil]);
        assert([files createDirectoryAtPath:elsewhere withIntermediateDirectories:YES attributes:nil error:nil]);
        assert([files createFileAtPath:[project stringByAppendingPathComponent:@".git"]
                             contents:[@"gitdir: worktree\n" dataUsingEncoding:NSUTF8StringEncoding] attributes:nil]);
        assert([files linkItemAtPath:source toPath:executable error:nil] || [files copyItemAtPath:source toPath:executable error:nil]);
        pid_t first = startPane(executable, inside);
        pid_t second = 0;
        @try {
            NSDictionary *observed = JardAnnotationTerminalContext(getpid(), @"OC | A disposable session", nil);
            assert([observed[@"folder"] isEqual:[NSURL fileURLWithPath:inside].URLByResolvingSymlinksInPath.path]);
            assert([observed[@"project"] isEqual:[NSURL fileURLWithPath:project].URLByResolvingSymlinksInPath.path]);
            assert([observed[@"session"] isEqual:@"ses_disposable"] && [observed[@"title"] isEqual:@"A disposable session"]);
            second = startPane(executable, elsewhere);
            assert(!JardAnnotationTerminalContext(getpid(), @"OpenCode", nil));
            assert([JardAnnotationTerminalContext(getpid(), @"OC | A disposable session", inside) isEqual:observed]);
        } @finally {
            kill(first, SIGTERM);
            waitpid(first, NULL, 0);
            if (second) {
                kill(second, SIGTERM);
                waitpid(second, NULL, 0);
            }
            [files removeItemAtPath:scratch error:nil];
        }
        puts("Terminal metadata used the real foreground process, matched a worktree, and rejected ambiguous panes.");
    }
}
