/* Account Switcher.exe: the portable copy's launcher. Starts the bundled windowless Python
   (runtime\pythonw.exe) on AccountSwitcher.pyw next to this exe, passing its arguments on,
   then exits. It carries the app icon, so Explorer, the Start menu and pins show it. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

static const wchar_t *arguments(void) {
    /* The command line after this exe's own (possibly quoted) path. */
    const wchar_t *line = GetCommandLineW();
    if (*line == L'"') {
        line++;
        while (*line && *line != L'"') line++;
        if (*line) line++;
    } else {
        while (*line && *line != L' ' && *line != L'\t') line++;
    }
    while (*line == L' ' || *line == L'\t') line++;
    return line;
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command, int show) {
    wchar_t folder[MAX_PATH], python[MAX_PATH], script[MAX_PATH], line[4 * MAX_PATH];
    DWORD length = GetModuleFileNameW(NULL, folder, MAX_PATH);
    if (!length || length >= MAX_PATH) return 1;
    wchar_t *slash = wcsrchr(folder, L'\\');
    if (!slash) return 1;
    *slash = 0;
    swprintf(python, MAX_PATH, L"%ls\\runtime\\pythonw.exe", folder);
    swprintf(script, MAX_PATH, L"%ls\\AccountSwitcher.pyw", folder);
    swprintf(line, 4 * MAX_PATH, L"\"%ls\" \"%ls\" %ls", python, script, arguments());
    AllowSetForegroundWindow(ASFW_ANY); /* the app may bring its window to the front */
    STARTUPINFOW startup = {sizeof(startup)};
    PROCESS_INFORMATION process;
    if (!CreateProcessW(python, line, NULL, NULL, FALSE, 0, NULL, folder, &startup, &process)) {
        MessageBoxW(NULL, L"Account Switcher couldn't start: runtime\\pythonw.exe is missing. Download it again.",
                    L"Account Switcher", MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
