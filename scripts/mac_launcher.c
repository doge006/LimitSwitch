/* LimitSwitch.app's own executable (macOS). It runs Python inside this process, so the
 * process macOS sees is the app itself (its bundle, name, icon and menu bar item); macOS 26+
 * gives no menu bar space to a bundle whose script hands over to another program.
 *
 * Nothing is compiled in: the paths come from Contents/Resources/launcher.conf, four lines
 * written by the installer:
 *   1. Python's library (the framework's Python, or libpython*.dylib)
 *   2. .venv/bin/python3 (Python then uses the .venv)
 *   3. LimitSwitch.pyw
 *   4. app.log (anything the app prints goes there)
 * A line may also be relative to Contents/Resources (the DMG's self-contained app, whose Python
 * and code are inside the bundle) or start with ~/ (the user's home).
 * Python is loaded at run time, so one build works with any Python 3.10+. The installer
 * builds this with the Mac's own clang; scripts/mac_launcher is the same source built by CI,
 * used when the local build tools can't link.
 *
 * Opened by the user it shows its window; --at-login (the login item) starts it quietly. */
#include <dlfcn.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

static void alert(const char *message) {
    char command[PATH_MAX + 512];
    snprintf(command, sizeof command,
             "/usr/bin/osascript -e 'display alert \"LimitSwitch did not start\" message \"%s\" as critical' >/dev/null 2>&1",
             message);
    system(command);
}

static int read_line(FILE *file, char *out, size_t size) {
    if (!fgets(out, (int)size, file)) return 0;
    out[strcspn(out, "\r\n")] = '\0';
    return out[0] != '\0';
}

/* An absolute path as is; ~/x in the home folder; anything else inside Contents/Resources. */
static void resolve(char *path, size_t size, const char *resources) {
    char given[PATH_MAX];
    if (path[0] == '/') return;
    snprintf(given, sizeof given, "%s", path);
    if (given[0] == '~' && given[1] == '/')
        snprintf(path, size, "%s/%s", getenv("HOME") ? getenv("HOME") : "", given + 2);
    else
        snprintf(path, size, "%s/%s", resources, given);
}

int main(int argc, char **argv) {
    char exe[PATH_MAX], app[PATH_MAX], resources[PATH_MAX + 64], conf[PATH_MAX + 96];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, app)) {
        alert("Couldn't find the app's own location.");
        return 1;
    }
    char *end = strstr(app, ".app/Contents/MacOS/");
    if (!end) {
        alert("The launcher must run from inside LimitSwitch.app.");
        return 1;
    }
    end[4] = '\0';
    snprintf(resources, sizeof resources, "%s/Contents/Resources", app);
    snprintf(conf, sizeof conf, "%s/launcher.conf", resources);

    char library[PATH_MAX], python[PATH_MAX], script[PATH_MAX], log[PATH_MAX];
    FILE *file = fopen(conf, "r");
    int ok = file && read_line(file, library, sizeof library) && read_line(file, python, sizeof python)
             && read_line(file, script, sizeof script) && read_line(file, log, sizeof log);
    if (file) fclose(file);
    if (!ok) {
        alert("Its settings are missing. Run Update.command in the LimitSwitch folder again.");
        return 1;
    }
    resolve(library, sizeof library, resources);
    resolve(python, sizeof python, resources);
    resolve(script, sizeof script, resources);
    resolve(log, sizeof log, resources);

    setenv("ACCOUNT_SWITCHER_APP", app, 1);
    setenv("__PYVENV_LAUNCHER__", python, 1);  /* Python uses the .venv, as with .venv/bin/python3 */
    char folder[PATH_MAX];
    snprintf(folder, sizeof folder, "%s", log);
    char *slash = strrchr(folder, '/');
    if (slash) {
        *slash = '\0';
        mkdir(folder, 0755); /* first start of the DMG's app: nothing has made it yet */
    }
    FILE *out = fopen(log, "a");
    if (out) {
        fclose(out);
        freopen(log, "a", stdout);
        freopen(log, "a", stderr);
    }

    void *handle = dlopen(library, RTLD_NOW | RTLD_GLOBAL);
    int (*py_main)(int, char **) = handle ? (int (*)(int, char **))dlsym(handle, "Py_BytesMain") : NULL;
    if (!py_main) {
        fprintf(stderr, "Startup failed: can't load Python from %s: %s\n", library, dlerror());
        alert("Its Python could not be loaded. Run Update.command in the LimitSwitch folder again.");
        return 1;
    }
    int quiet = argc > 1 && strcmp(argv[1], "--at-login") == 0;
    char *args[] = {argv[0], script, quiet ? NULL : "--show", NULL};
    return py_main(quiet ? 2 : 3, args);
}
