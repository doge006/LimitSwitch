"""Windows: give the full view's window LimitSwitch's own taskbar identity.

The app runs as pythonw.exe, so the taskbar would group its window under Python (its icon and
name). Setting an AppUserModelID of our own on the window (the documented per-window property
store) makes the taskbar treat it as its own app, and the relaunch properties give that button
our name and icon, also when pinned.
"""
import ctypes
from ctypes import wintypes

APP_ID = "LimitSwitch.App"  # a new ID also leaves the old name's cached taskbar icon behind
TITLE = "LimitSwitch"

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
