#import "jard_annotation_terminal.h"
#import <libproc.h>
#import <sys/proc_info.h>
#import <sys/sysctl.h>
#import <unistd.h>

BOOL JardIsAnnotationTerminal(NSString *bundleID) {
    return [@[@"com.apple.terminal", @"com.googlecode.iterm2", @"com.mitchellh.ghostty", @"net.kovidgoyal.kitty",
        @"com.github.wez.wezterm", @"org.alacritty", @"dev.warp.warp-stable", @"dev.warp.warp-preview"]
        containsObject:bundleID.lowercaseString ?: @""];
}

static BOOL processInfo(pid_t pid, struct proc_bsdinfo *info) {
    return pid > 0 && proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, info, sizeof(*info)) == sizeof(*info);
}

static NSString *canonicalDirectory(NSString *path) {
    if (!path.isAbsolutePath) return nil;
    NSURL *url = [NSURL fileURLWithPath:path isDirectory:YES].URLByResolvingSymlinksInPath;
    BOOL directory = NO;
    return [NSFileManager.defaultManager fileExistsAtPath:url.path isDirectory:&directory] && directory ? url.path : nil;
}

static NSString *workingDirectory(pid_t pid) {
    struct proc_vnodepathinfo info;
    if (proc_pidinfo(pid, PROC_PIDVNODEPATHINFO, 0, &info, sizeof(info)) != sizeof(info)) return nil;
    const char *path = info.pvi_cdir.vip_path;
    size_t length = strnlen(path, sizeof(info.pvi_cdir.vip_path));
    if (length == sizeof(info.pvi_cdir.vip_path)) return nil;
    return canonicalDirectory([[NSFileManager defaultManager] stringWithFileSystemRepresentation:path length:length]);
}

static NSString *projectRoot(NSString *folder) {
    NSString *home = canonicalDirectory(NSHomeDirectory());
    if (!folder.length || [folder isEqual:@"/"] || [folder isEqual:home]) return nil;
    for (NSString *item = folder; item.length && ![item isEqual:@"/"] && ![item isEqual:home];
        item = item.stringByDeletingLastPathComponent) {
        if ([NSFileManager.defaultManager fileExistsAtPath:[item stringByAppendingPathComponent:@".git"]])
            return item;
    }
    return folder;
}

static NSArray<NSString *> *commandArguments(pid_t pid) {
    int query[] = {CTL_KERN, KERN_PROCARGS2, pid};
    size_t size = 0;
    if (sysctl(query, 3, NULL, &size, NULL, 0) || size < sizeof(int) || size > 256 * 1024) return @[];
    NSMutableData *buffer = [NSMutableData dataWithLength:size];
    if (sysctl(query, 3, buffer.mutableBytes, &size, NULL, 0) || size < sizeof(int)) return @[];
    const char *bytes = buffer.bytes, *end = bytes + size;
    int count = 0;
    memcpy(&count, bytes, sizeof(count));
    if (count <= 0 || count > 4096) return @[];
    const char *position = memchr(bytes + sizeof(count), 0, size - sizeof(count));
    if (!position) return @[];
    while (position < end && !*position) position++;
    NSMutableArray *args = NSMutableArray.array;
    for (int index = 0; index < count && position < end; index++) {
        const char *next = memchr(position, 0, end - position);
        if (!next) break;
        NSString *arg = [[NSString alloc] initWithBytes:position length:next - position encoding:NSUTF8StringEncoding];
        [args addObject:arg ?: @""];
        position = next + 1;
    }
    return args;
}

static BOOL isOpenCode(NSString *name) {
    return [@[@"opencode", @"opencode-cli", @"opencode-v2", @"opencode-beta"] containsObject:name.lastPathComponent];
}

static NSString *commandSession(NSArray<NSString *> *args) {
    for (NSUInteger i = 1; i < args.count; i++) {
        NSString *option = args[i];
        if ([option isEqual:@"--"]) break;
        NSString *value = nil;
        if (([option isEqual:@"--session"] || [option isEqual:@"-s"]) && i + 1 < args.count) value = args[++i];
        else if ([option hasPrefix:@"--session="]) value = [option substringFromIndex:@"--session=".length];
        if (value.length && [value rangeOfString:@"^ses_[A-Za-z0-9_-]{1,123}$" options:NSRegularExpressionSearch].location != NSNotFound)
            return value;
    }
    return nil;
}

static NSDictionary *foregroundContext(struct proc_bsdinfo process) {
    if (process.e_tdev == 0 || process.e_tdev == UINT32_MAX || process.e_tpgid > INT_MAX) return nil;
    pid_t leader = (pid_t)process.e_tpgid;
    struct proc_bsdinfo found;
    if (!processInfo(leader, &found) || found.pbi_uid != geteuid() || found.pbi_pgid != (uint32_t)leader ||
        found.e_tdev != process.e_tdev || found.e_tpgid != process.e_tpgid) return nil;
    NSString *folder = workingDirectory(leader);
    if (!folder) return nil;
    char path[PROC_PIDPATHINFO_MAXSIZE];
    int length = proc_pidpath(leader, path, sizeof(path));
    NSString *executable = length > 0 ? [NSString stringWithUTF8String:path].lastPathComponent : nil;
    BOOL openCode = isOpenCode(executable ?: @"");
    NSArray *args = @[];
    if (openCode || [@[@"bun", @"node", @"deno"] containsObject:executable ?: @""]) {
        args = commandArguments(leader);
        openCode |= args.count > 1 && isOpenCode(args[1]);
    }
    struct proc_bsdinfo confirmed;
    if (!processInfo(leader, &confirmed) || confirmed.pbi_start_tvsec != found.pbi_start_tvsec ||
        confirmed.pbi_start_tvusec != found.pbi_start_tvusec || confirmed.e_tdev != found.e_tdev ||
        confirmed.e_tpgid != found.e_tpgid) return nil;
    NSString *session = openCode ? commandSession(args) : nil;
    return @ {@"folder": folder, @"openCode": @(openCode), @"session": session ?: @""};
}

static NSArray<NSDictionary *> *terminalProcesses(pid_t terminalPID) {
    enum { MAX_PROCESSES = 384, MAX_CHILDREN = 128 };
    struct proc_bsdinfo terminal;
    if (!processInfo(terminalPID, &terminal) || terminal.pbi_uid != geteuid()) return @[];
    NSMutableArray<NSNumber *> *queue = [NSMutableArray arrayWithObject:@(terminalPID)];
    NSMutableSet *visited = [NSMutableSet setWithObject:@(terminalPID)];
    NSMutableDictionary<NSNumber *, NSDictionary *> *surfaces = NSMutableDictionary.dictionary;
    for (NSUInteger index = 0; index < queue.count; index++) {
        pid_t parent = queue[index].intValue;
        pid_t children[MAX_CHILDREN] = {0};
        int count = proc_listchildpids(parent, children, sizeof(children));
        if (count >= MAX_CHILDREN) return @[];
        for (int j = 0; j < count; j++) {
            pid_t child = children[j];
            NSNumber *key = @(child);
            if (child <= 0 || [visited containsObject:key]) continue;
            if (queue.count >= MAX_PROCESSES) return @[];
            [visited addObject:key];
            [queue addObject:key];
            struct proc_bsdinfo info;
            if (!processInfo(child, &info) || info.pbi_ppid != (uint32_t)parent ||
                info.e_tdev == 0 || info.e_tdev == UINT32_MAX || info.e_tpgid == 0) continue;
            NSNumber *tty = @(info.e_tdev);
            if (!surfaces[tty]) surfaces[tty] = foregroundContext(info) ?: @{};
        }
    }
    // An invisible or concurrently closed pane is still ambiguous without a matching directory hint.
    return surfaces.allValues;
}

NSDictionary *JardAnnotationTerminalContext(pid_t terminalPID, NSString *title, NSString *directoryHint) {
    NSArray<NSDictionary *> *surfaces = terminalProcesses(terminalPID);
    NSString *hint = canonicalDirectory(directoryHint);
    NSString *tui = [title hasPrefix:@"OC | "] ? [title substringFromIndex:5] : nil;
    NSMutableArray *matches = NSMutableArray.array;
    for (NSDictionary *pane in surfaces) {
        if (hint && ![pane[@"folder"] isEqual:hint]) continue;
        if ((tui.length || [title isEqual:@"OpenCode"]) && ![pane[@"openCode"] boolValue] && pane[@"folder"]) continue;
        [matches addObject:pane];
    }
    if (matches.count != 1) return nil;
    NSDictionary *selected = matches.firstObject;
    NSString *folder = selected[@"folder"];
    NSString *project = projectRoot(folder);
    if (!project) return nil;
    NSMutableDictionary *value = [@{@"folder": folder, @"project": project} mutableCopy];
    if ([selected[@"openCode"] boolValue]) {
        if (tui.length && tui.length <= 160) value[@"title"] = tui;
        if ([selected[@"session"] length]) value[@"session"] = selected[@"session"];
    }
    return value;
}
