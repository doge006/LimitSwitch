"""Windows: give the full view's window Account Switcher's own taskbar identity.

The full view opens as an Edge app window, which the taskbar groups under Edge (Edge's icon
and name). Setting an AppUserModelID of our own on that window (the documented per-window
property store) makes the taskbar treat it as its own app, and the relaunch properties give
that button our name and icon, also when pinned. The window icon is set as well.
"""
import ctypes
from ctypes import wintypes
import threading
import time

APP_ID = "AccountSwitcher.App"
TITLE = "Account Switcher"
WINDOW_CLASS = "Chrome_WidgetWin_1"  # Edge / Chromium top-level windows

WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
VT_LPWSTR = 31


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    def __init__(self, text):
        super().__init__()
        ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(self))


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("value", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


APP_MODEL = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
PKEY = {"command": 2, "icon": 3, "name": 4, "id": 5}  # RelaunchCommand, RelaunchIconResource, RelaunchDisplayNameResource, ID
IID_IPROPERTYSTORE = "{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}"


def _key(name):
    return PROPERTYKEY(GUID(APP_MODEL), PKEY[name])


def _store(hwnd):
    """The window's IPropertyStore as (pointer, vtable)."""
    store = ctypes.c_void_p()
    ctypes.oledll.shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(GUID(IID_IPROPERTYSTORE)),
                                                      ctypes.byref(store))
    vtable = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return store, vtable


def _method(vtable, index, *argtypes):
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])


def set_identity(hwnd, relaunch, icon):
    """Own AppUserModelID, name and icon for this window's taskbar button."""
    store, vtable = _store(hwnd)
    set_value = _method(vtable, 6, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
    commit = _method(vtable, 7)
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    try:
        # The relaunch properties first: the taskbar reads them when the ID changes.
        for name, value in (("command", relaunch), ("name", TITLE), ("icon", f"{icon},0"), ("id", APP_ID)):
            set_value(store, ctypes.byref(_key(name)), ctypes.byref(PROPVARIANT(VT_LPWSTR, 0, 0, 0, value, None)))
        commit(store)
    finally:
        release(store)


def get_identity(hwnd):
    """The window's AppUserModelID (tests)."""
    store, vtable = _store(hwnd)
    get_value = _method(vtable, 5, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT))
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
    value = PROPVARIANT()
    try:
        get_value(store, ctypes.byref(_key("id")), ctypes.byref(value))
        return value.value if value.vt == VT_LPWSTR else None
    finally:
        release(store)


def set_icon(hwnd, icon):
    user32 = ctypes.windll.user32
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    for which, size in ((ICON_BIG, 32), (ICON_SMALL, 16)):
        handle = user32.LoadImageW(None, str(icon), IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if handle:
            user32.SendMessageW(hwnd, WM_SETICON, which, handle)


def edge_windows():
    """Every titled top-level Edge / Chromium window, shown or not: {hwnd: (visible, title)}."""
    user32 = ctypes.windll.user32
    found = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == WINDOW_CLASS:
            user32.GetWindowTextW(hwnd, title, 256)
            if title.value:
                found[hwnd] = (bool(user32.IsWindowVisible(hwnd)), title.value)
        return True

    user32.EnumWindows(each, 0)
    return found


def find_windows(address=None):
    """Shown Edge windows with the full view: its page title is the app's name."""
    return [hwnd for hwnd, (visible, title) in edge_windows().items() if visible and title.startswith(TITLE)]


def fit_window(hwnd, size):
    """Size a window to (width, height) logical px, centred in its display's work area. Edge
    remembers an app window's last size and ignores --window-size, so this sets it on open."""
    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(hwnd, 2)
    info = (ctypes.c_long * 10)()
    info[0] = ctypes.sizeof(info)  # MONITORINFO: cbSize, rcMonitor, rcWork, dwFlags
    if not user32.GetMonitorInfoW(monitor, info):
        return
    left, top, right, bottom = info[5], info[6], info[7], info[8]
    scale = user32.GetDpiForWindow(hwnd) / 96 if hasattr(user32, "GetDpiForWindow") else 1
    width = min(round(size[0] * (scale or 1)), right - left)
    height = min(round(size[1] * (scale or 1)), bottom - top)
    user32.SetWindowPos(hwnd, None, left + (right - left - width) // 2, top + (bottom - top - height) // 2,
                        width, height, 0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE


def focus_full_view(address=None):
    """Bring an open full view to the front (restored if minimised). False when none is open."""
    user32 = ctypes.windll.user32
    for hwnd in find_windows(address):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    return False


def brand_full_view(relaunch, icon, seconds=15, size=None, address=None, before=()):
    """Catch the full view's new window (not one in before) and give it our identity and, when
    given, its size and place. Edge creates the window hidden and shows it a moment later, titled
    by its host ("127.0.0.1_/") until the page loads, so this polls closely and changes it while
    still hidden: it then appears once, as it should, with no hide / show or jump. Runs in the
    background; does nothing when there's no such window."""
    host = (address or "").split(":")[0]
    before = set(before)

    def ours(title):
        return title.startswith(TITLE) or bool(host and title.startswith(host))

    def work():
        user32 = ctypes.windll.user32
        ctypes.oledll.ole32.CoInitialize(None)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            found = [(hwnd, visible) for hwnd, (visible, title) in edge_windows().items()
                     if hwnd not in before and ours(title)]
            if found:
                hwnd, visible = found[0]
                if visible:  # caught late: one hidden change rather than several visible ones
                    user32.ShowWindow(hwnd, 0)  # SW_HIDE
                try:
                    set_identity(hwnd, relaunch, icon)
                    set_icon(hwnd, icon)
                    if size and user32.IsZoomed(hwnd) == 0:
                        fit_window(hwnd, size)
                except OSError:
                    pass
                if visible:
                    user32.ShowWindow(hwnd, 5)  # SW_SHOW
                else:
                    settle(hwnd)
                user32.SetForegroundWindow(hwnd)
                return
            time.sleep(0.005)  # Edge shows it ~0.3 s after creating it

    def settle(hwnd):
        """Wait for Edge to show the window; should it restore its own bounds on showing, fit it again."""
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        placed = (rect.left, rect.top, rect.right, rect.bottom)
        end = time.monotonic() + 5
        while time.monotonic() < end and user32.IsWindow(hwnd) and not user32.IsWindowVisible(hwnd):
            time.sleep(0.005)
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if size and (rect.left, rect.top, rect.right, rect.bottom) != placed and user32.IsZoomed(hwnd) == 0:
            fit_window(hwnd, size)

    threading.Thread(target=work, daemon=True, name="full-view-identity").start()
