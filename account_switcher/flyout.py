"""Windows host for the tray flyout: a per-pixel-alpha layered popup, like the shell's own.

The window exists only while open. It lives on the tray's thread (pystray's message loop
dispatches its messages), uses a timer only during the ~0.17 s open/close animation, and
redraws only when the hovered item or the app state changes.
"""
import ctypes
from ctypes import wintypes
import time

from . import flyout_render as fr

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_POPUP = 0x80000000
WS_EX_LAYERED, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = 0x00080000, 0x00000080, 0x00000008
WM_DESTROY, WM_ACTIVATE, WM_SETCURSOR, WM_KEYDOWN, WM_TIMER = 0x0002, 0x0006, 0x0020, 0x0100, 0x0113
WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSELEAVE = 0x0200, 0x0201, 0x0202, 0x02A3
WM_APP_REFRESH, WM_APP_CLOSE = 0x8000 + 1, 0x8000 + 2
SW_SHOWNA, VK_ESCAPE, ULW_ALPHA, TME_LEAVE = 8, 0x1B, 0x2, 0x2
IDC_ARROW, IDC_HAND = 32512, 32649
MONITOR_DEFAULTTONEAREST = 2
CLASS_NAME = "AccountSwitcherFlyout"
TIMER_ID = 1


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HANDLE), ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HANDLE)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def _sig(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)


H = wintypes.HANDLE
_sig(user32.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(WNDCLASSEXW))
_sig(user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, H, wintypes.HINSTANCE, ctypes.c_void_p)
_sig(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.ShowWindow, wintypes.BOOL, wintypes.HWND, ctypes.c_int)
_sig(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
_sig(user32.LoadCursorW, H, wintypes.HINSTANCE, ctypes.c_void_p)
_sig(user32.SetCursor, H, H)
_sig(user32.SetTimer, ctypes.c_size_t, wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
_sig(user32.KillTimer, wintypes.BOOL, wintypes.HWND, ctypes.c_size_t)
_sig(user32.PostMessageW, wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
_sig(user32.TrackMouseEvent, wintypes.BOOL, ctypes.POINTER(TRACKMOUSEEVENT))
_sig(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
_sig(user32.MonitorFromPoint, H, wintypes.POINT, wintypes.DWORD)
_sig(user32.GetMonitorInfoW, wintypes.BOOL, H, ctypes.POINTER(MONITORINFO))
_sig(user32.GetDC, wintypes.HDC, wintypes.HWND)
_sig(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_sig(user32.UpdateLayeredWindow, wintypes.BOOL, wintypes.HWND, wintypes.HDC, ctypes.POINTER(wintypes.POINT),
     ctypes.POINTER(wintypes.SIZE), wintypes.HDC, ctypes.POINTER(wintypes.POINT), wintypes.DWORD,
     ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD)
_sig(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_sig(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)
_sig(gdi32.CreateDIBSection, wintypes.HBITMAP, wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
     ctypes.POINTER(ctypes.c_void_p), H, wintypes.DWORD)
_sig(gdi32.SelectObject, H, wintypes.HDC, H)
_sig(gdi32.DeleteObject, wintypes.BOOL, H)
_sig(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)


def enable_dpi_awareness():
    """Per-monitor DPI awareness so the flyout is drawn sharp at 125/150/200 %."""
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def monitor_at_cursor():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    scale = 1.0
    try:
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        if ctypes.windll.shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == 0:
            scale = dpi_x.value / 96
    except (AttributeError, OSError):
        pass
    return info.rcMonitor, info.rcWork, scale


def place(monitor, work, width, height, scale):
    """Top-left of the image so the panel sits in the taskbar's corner, 12 px from the edges."""
    gap, margin = round(12 * scale), round(fr.MARGIN * scale)
    x = work.right - gap - width + margin
    y = work.bottom - gap - height + margin
    edge = "bottom"
    if work.left > monitor.left:
        x, edge = work.left + gap - margin, "left"
    elif work.top > monitor.top:
        y, edge = work.top + gap - margin, "top"
    elif work.right < monitor.right:
        edge = "right"
    return x, y, edge


class Flyout:
    _registered = False
    _proc = None

    def __init__(self, tray):
        self.tray = tray
        self.hwnd = None
        self.hover = None
        self.pressed = None
        self.hits = []
        self.closing = False
        self.closed_at = self.opened_at = 0.0
        self.anim = None
        self.tracking = False

    # ---------- public (tray thread) ----------
    def toggle(self):
        if self.hwnd and not self.closing:
            self.close()
        elif time.monotonic() - self.closed_at > 0.3:  # the click that dismissed it
            self.open()

    def open(self):
        if self.hwnd:
            self._destroy()
        self._register()
        self.monitor, self.work, self.scale = monitor_at_cursor()
        self.hover = self.pressed = None
        image, self.hits = fr.render(self.tray.state, None, self.scale)
        self.size = image.size
        self.x, self.y, edge = place(self.monitor, self.work, *image.size, self.scale)
        self.slide = {"bottom": (0, 1), "top": (0, -1), "left": (-1, 0), "right": (1, 0)}[edge]
        self.hwnd = user32.CreateWindowExW(WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST, CLASS_NAME,
                                           "Account Switcher", WS_POPUP, self.x, self.y, *image.size,
                                           None, None, kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            return
        self.closing = False
        self.opened_at = time.monotonic()
        self._push(image, 0)
        user32.ShowWindow(self.hwnd, SW_SHOWNA)
        user32.SetForegroundWindow(self.hwnd)  # so clicking elsewhere deactivates and closes it
        self._animate(0.0, 1.0, 0.17)

    def close(self):
        if self.hwnd and not self.closing:
            self.closing = True
            self.closed_at = time.monotonic()
            self._animate(1.0, 0.0, 0.11)

    def dismiss(self):
        """Any thread: remove the window immediately (used on quit)."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_CLOSE, 0, 0)

    def state_changed(self):
        """Any thread: ask the window's own thread to redraw."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_APP_REFRESH, 0, 0)

    # ---------- drawing ----------
    def _redraw(self):
        if not self.hwnd:
            return
        image, self.hits = fr.render(self.tray.state, self.hover, self.scale)
        if image.size != self.size:  # content height changed: keep the bottom edge anchored
            dy = image.size[1] - self.size[1]
            self.size = image.size
            if self.slide[1] >= 0:
                self.y -= dy
        self._push(image, self.alpha)

    def _push(self, image, alpha, offset=(0, 0)):
        """Blit a premultiplied-BGRA copy of `image` to the layered window."""
        self.alpha = alpha
        self.image = image
        width, height = image.size
        screen = user32.GetDC(None)
        memory = gdi32.CreateCompatibleDC(screen)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth, info.bmiHeader.biHeight = width, -height  # top-down
        info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(screen, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if bitmap and bits.value:
            data = image.convert("RGBa").tobytes("raw", "BGRa")
            ctypes.memmove(bits, data, len(data))
            old = gdi32.SelectObject(memory, bitmap)
            blend = BLENDFUNCTION(0, 0, max(0, min(255, round(alpha * 255))), 1)
            user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(wintypes.POINT(self.x + offset[0], self.y + offset[1])),
                                       ctypes.byref(wintypes.SIZE(width, height)), memory,
                                       ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
            gdi32.SelectObject(memory, old)
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)

    # ---------- animation (timer only while moving) ----------
    def _animate(self, start, end, duration):
        self.anim = (start, end, time.perf_counter(), duration)
        user32.SetTimer(self.hwnd, TIMER_ID, 10, None)
        self._tick()

    def _tick(self):
        start, end, began, duration = self.anim
        progress = min(1.0, (time.perf_counter() - began) / duration)
        eased = 1 - (1 - progress) ** 3
        value = start + (end - start) * eased
        travel = round(8 * self.scale * (1 - value))  # short slide toward the taskbar edge
        self._push(self.image, value, (self.slide[0] * travel, self.slide[1] * travel))
        if progress >= 1:
            user32.KillTimer(self.hwnd, TIMER_ID)
            self.anim = None
            if end == 0.0:
                self._destroy()

    # ---------- input ----------
    def _logical(self, lparam):
        x = ctypes.c_short(lparam & 0xFFFF).value / self.scale
        y = ctypes.c_short((lparam >> 16) & 0xFFFF).value / self.scale
        return x, y

    def _set_hover(self, action):
        if action != self.hover:
            self.hover = action
            self._redraw()

    def _activate(self, action):
        tray = self.tray
        if action == "full":
            self.close()
            tray.open_full_view()
        elif action == "quit":
            self._destroy()
            tray.quit()
        elif action.startswith("swap:"):
            tray.act("swap", {"id": action[5:]})
        elif action.startswith("toggle:"):
            key = action[7:]
            prefs = {"autoSwap": tray.state["autoSwap"], "afk": tray.state["afk"]}
            prefs[key] = not prefs[key]
            tray.act("preferences", prefs)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TIMER and wparam == TIMER_ID and self.anim:
            self._tick()
            return 0
        if msg == WM_APP_CLOSE:
            self._destroy()
            return 0
        if msg == WM_APP_REFRESH:
            if not self.closing:
                self._redraw()
            return 0
        if msg == WM_ACTIVATE and (wparam & 0xFFFF) == 0:  # WA_INACTIVE: clicked elsewhere
            # Focus can bounce while the tray click is still being processed; only a
            # deactivation after the flyout has settled means the user clicked away.
            if time.monotonic() - self.opened_at > 0.25:
                self.close()
            return 0
        if msg == WM_KEYDOWN and wparam == VK_ESCAPE:
            self.close()
            return 0
        if msg == WM_MOUSEMOVE:
            if not self.tracking:
                track = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, hwnd, 0)
                self.tracking = bool(user32.TrackMouseEvent(ctypes.byref(track)))
            if not self.closing:
                self._set_hover(fr.hit_test(self.hits, *self._logical(lparam)))
            return 0
        if msg == WM_MOUSELEAVE:
            self.tracking = False
            if not self.closing:
                self._set_hover(None)
            return 0
        if msg == WM_SETCURSOR:
            user32.SetCursor(user32.LoadCursorW(None, ctypes.c_void_p(IDC_HAND if self.hover else IDC_ARROW)))
            return 1
        if msg == WM_LBUTTONDOWN:
            self.pressed = fr.hit_test(self.hits, *self._logical(lparam))
            return 0
        if msg == WM_LBUTTONUP:
            action = fr.hit_test(self.hits, *self._logical(lparam))
            if action and action == self.pressed and not self.closing:
                self._activate(action)
            self.pressed = None
            return 0
        if msg == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _destroy(self):
        if self.hwnd:
            hwnd, self.hwnd = self.hwnd, None
            user32.KillTimer(hwnd, TIMER_ID)
            user32.DestroyWindow(hwnd)
        self.anim, self.tracking, self.closing = None, False, False

    def _register(self):
        if Flyout._registered:
            Flyout._active = self
            return
        Flyout._active = self

        def proc(hwnd, msg, wparam, lparam):
            owner = Flyout._active
            if owner is not None and owner.hwnd in (None, hwnd):
                try:
                    return owner._wndproc(hwnd, msg, wparam, lparam)
                except Exception:  # never let a Python error escape into Win32
                    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        Flyout._proc = WNDPROC(proc)  # keep a reference for the process lifetime
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = Flyout._proc
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
        wc.lpszClassName = CLASS_NAME
        user32.RegisterClassExW(ctypes.byref(wc))
        Flyout._registered = True
