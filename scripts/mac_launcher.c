/* Account Switcher.app's own executable (macOS). It runs Python inside this process, so the
 * process macOS sees is the app itself (its bundle, name and icon), which macOS 26 needs to
 * give the app a place in the menu bar. The installer compiles it with the paths below.
 *
 *   ACCOUNT_SWITCHER_VENV_PYTHON  .venv/bin/python3 (Python then uses the .venv)
 *   ACCOUNT_SWITCHER_SCRIPT       AccountSwitcher.pyw
 *   ACCOUNT_SWITCHER_LOG          app.log (anything the app prints goes there)
 *
 * Opened by the user it shows its window; --at-login (the login item) starts it quietly. */
#include <Python.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    char app[PATH_MAX], resolved[PATH_MAX];
    uint32_t size = sizeof(app);
    if (_NSGetExecutablePath(app, &size) == 0 && realpath(app, resolved)) {
        char *end = strstr(resolved, ".app/Contents/MacOS/");
        if (end) {
            end[4] = '\0';
            setenv("ACCOUNT_SWITCHER_APP", resolved, 1);
        }
    }
    setenv("__PYVENV_LAUNCHER__", ACCOUNT_SWITCHER_VENV_PYTHON, 1);
    FILE *log = fopen(ACCOUNT_SWITCHER_LOG, "a");
    if (log) {
        fclose(log);
        freopen(ACCOUNT_SWITCHER_LOG, "a", stdout);
        freopen(ACCOUNT_SWITCHER_LOG, "a", stderr);
    }
    int quiet = argc > 1 && strcmp(argv[1], "--at-login") == 0;
    char *args[] = {argv[0], ACCOUNT_SWITCHER_SCRIPT, quiet ? NULL : "--show", NULL};
    return Py_BytesMain(quiet ? 2 : 3, args);
}
