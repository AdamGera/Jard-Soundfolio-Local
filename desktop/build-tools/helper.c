// A signed executable entry point for the Python helper and native UI client.
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX], resolved[PATH_MAX], python[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) || !realpath(executable, resolved)) return 1;
    const int is_client = !strcmp(basename(argv[0]), "jard-ui");
    const char *helpers = dirname(resolved);
    if (snprintf(python, sizeof(python), "%s/../Resources/PythonRuntime/bin/python3.13", helpers) >= sizeof(python) ||
        snprintf(script, sizeof(script), "%s/../Resources/engine/desktop/build-tools/helper.py", helpers) >= sizeof(script)) return 1;
    char **args = calloc((size_t)argc + 6, sizeof(*args));
    if (!args) return 1;
    args[0] = python;
    args[1] = "-EsBu";
    args[2] = script;
    args[3] = is_client ? "ui" : "engine";
    for (int n = 1; n < argc; n++) args[n + 3] = argv[n];
    execv(python, args);
    perror("Jaro runtime");
    return 1;
}
