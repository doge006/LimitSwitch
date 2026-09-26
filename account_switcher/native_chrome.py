"""Windows-owned dark frame, rounded corners and popup shadow. No polling."""
import ctypes
from ctypes import wintypes


def apply_chrome(window, popup=False):
    window.update_idletasks()
    user = ctypes.windll.user32
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    hwnd = user.GetParent(window.winfo_id()) or window.winfo_id()
    dwm = ctypes.windll.dwmapi
    dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    results = {}
    for name, attribute, number in [('dark', 20, 1), ('corners', 33, 2), ('border', 34, 0x424242), ('caption', 35, 0x1e1e1e)]:
        value = ctypes.c_int(number)
        results[name] = dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))
    if popup:
        # A frame style lets DWM supply its anti-aliased corners and shadow.
        # Tk still owns the undecorated client area and its event handling.
        user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowLongW.restype = ctypes.c_long
        user.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        style = user.GetWindowLongW(hwnd, -16)
        user.SetWindowLongW(hwnd, -16, style | 0x00040000)  # WS_THICKFRAME
        policy = ctypes.c_int(2)  # DWMNCRP_ENABLED
        dwm.DwmSetWindowAttribute(hwnd, 2, ctypes.byref(policy), ctypes.sizeof(policy))
        margins = (ctypes.c_int * 4)(1, 1, 1, 1)
        dwm.DwmExtendFrameIntoClientArea.argtypes = [wintypes.HWND, ctypes.c_void_p]
        results['shadow'] = dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)  # frame changed, no move/size/z-order
    return results
