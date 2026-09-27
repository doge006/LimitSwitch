"""Windows taskbar view: the account in use for each provider, drawn in the taskbar's empty space.

One small block per provider (Claude from the left, Codex from the right), each the compact
panel's row laid out sideways: icon, name, and a column per limit with "% left" and
"resets in". The blocks are layered, click-through-free popups that never take focus; a
click opens the panel above the block, a right-click opens the menu.

Nothing polls. The layout is worked out again only when the taskbar can have changed
(a window opened or closed, display or theme settings changed, Explorer restarted) and once
a minute with the reset times. The blocks are owned by the taskbar, so Windows keeps them
just above it and takes them down with it when a full-screen app covers it; screenshot
tools and other overlays never make them hide and come back.
"""
import ctypes
from ctypes import wintypes
import logging
import math
import time
import winreg

from . import flyout as fl
from . import flyout_render as fr
from .placement import free_gaps, place_blocks

user32, kernel32 = fl.user32, fl.kernel32
log = logging.getLogger("account_switcher.taskbar")

WM_APP_TASKBAR = 0x8000 + 20
WM_TIMER, WM_SETTINGCHANGE, WM_DISPLAYCHANGE = 0x0113, 0x001A, 0x007E
WM_RBUTTONUP, WM_MOUSEACTIVATE, MA_NOACTIVATE, WM_DESTROY = 0x0205, 0x0021, 3, 0x0002
WS_EX_NOACTIVATE = 0x08000000
TIMER_LAYOUT, TIMER_MINUTE = 71, 72
HSHELL_WINDOWCREATED, HSHELL_WINDOWDESTROYED = 1, 2
PROVIDER_ORDER = ("claude", "codex")


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uCallbackMessage", wintypes.UINT),
                ("uEdge", wintypes.UINT), ("rc", wintypes.RECT), ("lParam", wintypes.LPARAM)]


sig = fl._sig
sig(user32.FindWindowW, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
sig(user32.FindWindowExW, wintypes.HWND, wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
sig(user32.GetWindowRect, wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT))
sig(user32.IsWindowVisible, wintypes.BOOL, wintypes.HWND)
sig(user32.RegisterShellHookWindow, wintypes.BOOL, wintypes.HWND)
sig(user32.DeregisterShellHookWindow, wintypes.BOOL, wintypes.HWND)
sig(user32.RegisterWindowMessageW, wintypes.UINT, wintypes.LPCWSTR)
shell32 = ctypes.WinDLL("shell32")
sig(shell32.SHAppBarMessage, ctypes.c_size_t, wintypes.DWORD, ctypes.POINTER(APPBARDATA))


def window_rect(hwnd):
    rect = wintypes.RECT()
    if hwnd and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return rect.left, rect.top, rect.right, rect.bottom
    return None


def setting(name, default, key=r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
            return winreg.QueryValueEx(handle, name)[0]
    except OSError:
        return default


def light_taskbar():
    return bool(setting("SystemUsesLightTheme", 0, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"))


def auto_hide():
    data = APPBARDATA(ctypes.sizeof(APPBARDATA))
    return bool(shell32.SHAppBarMessage(4, ctypes.byref(data)) & 1)  # ABM_GETSTATE & ABS_AUTOHIDE


# ---------- the taskbar's own buttons, through UI Automation (plain COM calls) ----------
class _COM:
    """Just enough IUIAutomation to list the taskbar's buttons and where they are."""
    CLSID = "{ff48dba4-60ef-4201-aa87-54103eef594e}"   # CUIAutomation
    IID = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"     # IUIAutomation
    # Buttons (task buttons, Start, Search, Task view, Widgets), list items and menu items,
    # split buttons, and the search box.
    KINDS = {50000, 50004, 50007, 50011, 50031}
    ole32 = None
    automation = None

    @staticmethod
    def call(obj, index, argtypes=(), *args, restype=ctypes.c_long):
        """Method `index` of a COM object's vtable."""
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])(obj, *args)

    @classmethod
    def release(cls, obj):
        if obj:
            cls.call(obj, 2, restype=ctypes.c_ulong)

    @classmethod
    def start(cls):
        if cls.automation:
            return cls.automation
        cls.ole32 = ctypes.OleDLL("ole32")
        try:
            cls.ole32.CoInitializeEx(None, 2)  # apartment-threaded, the tray thread's
        except OSError:
            pass  # already initialised differently: still usable
        clsid, iid = fl.GUID(), fl.GUID()
        cls.ole32.CLSIDFromString(cls.CLSID, ctypes.byref(clsid))
        cls.ole32.CLSIDFromString(cls.IID, ctypes.byref(iid))
        automation = ctypes.c_void_p()
        cls.ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(automation))
        cls.automation = automation
        return automation

    @classmethod
    def buttons(cls, hwnd):
        """[(left, right, top, bottom)] of the button-like elements under a window."""
        automation = cls.start()
        element, condition, found = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        spans = []
        try:
            out = (ctypes.POINTER(ctypes.c_void_p),)
            if cls.call(automation, 6, (wintypes.HWND,) + out, hwnd, ctypes.byref(element)) < 0 or not element:  # ElementFromHandle
                return None
            if cls.call(automation, 21, out, ctypes.byref(condition)) < 0:  # CreateTrueCondition
                return None
            if cls.call(element, 6, (ctypes.c_int, ctypes.c_void_p) + out, 4, condition, ctypes.byref(found)) < 0 or not found:
                return None  # FindAll(TreeScope_Descendants)
            count = ctypes.c_int()
            cls.call(found, 3, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(count))  # get_Length
            for i in range(min(count.value, 400)):
                item = ctypes.c_void_p()
                if cls.call(found, 4, (ctypes.c_int,) + out, i, ctypes.byref(item)) < 0 or not item:  # GetElement
                    continue
                try:
                    kind, rect = ctypes.c_int(), wintypes.RECT()
                    cls.call(item, 21, (ctypes.POINTER(ctypes.c_int),), ctypes.byref(kind))  # get_CurrentControlType
                    if kind.value in cls.KINDS and cls.call(item, 43, (ctypes.POINTER(wintypes.RECT),), ctypes.byref(rect)) >= 0:
                        spans.append((rect.left, rect.right, rect.top, rect.bottom))
                finally:
                    cls.release(item)
        finally:
            for obj in (found, condition, element):
                cls.release(obj)
        return spans


class Bar:
    """One taskbar's geometry, in physical pixels."""

    def __init__(self, hwnd, key, label, rect, scale, left, right, occupied, light, measured):
        self.hwnd, self.key, self.label = hwnd, key, label
        self.rect, self.scale, self.light, self.measured = rect, scale, light, measured
        self.left, self.right, self.occupied = left, right, occupied

    @property
    def height(self):
        """Block height in logical px: the taskbar's less 2 px above and below (44 on Windows 11)."""
        return max(30, min(44, round((self.rect[3] - self.rect[1]) / self.scale) - 4))


def taskbars():
    """[(hwnd, key, label)]: the main taskbar, then those on other displays (when Windows shows
    the taskbar on all displays), named by where they are next to the main one."""
    main = user32.FindWindowW("Shell_TrayWnd", None)
    found = [(main, "main", "Main display")] if main else []
    main_rect = window_rect(main)
    others = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        name = ctypes.create_unicode_buffer(40)
        user32.GetClassNameW(hwnd, name, 40)
        rect = window_rect(hwnd)
        if name.value == "Shell_SecondaryTrayWnd" and rect and user32.IsWindowVisible(hwnd):
            others.append((rect, hwnd))
        return True

    user32.EnumWindows(each, 0)
    counts = {}
    for rect, hwnd in sorted(others):
        side = "left" if main_rect and rect[0] < main_rect[0] else "right"
        counts[side] = counts.get(side, 0) + 1
        suffix = f" {counts[side]}" if counts[side] > 1 else ""
        found.append((hwnd, side + suffix.replace(" ", "-"), f"{side.title()} display{suffix}"))
    return found


def read_bar(hwnd, key="main", label="Main display"):
    """Where a taskbar is and which parts of it are free, or None (hidden, vertical, auto-hide)."""
    rect = window_rect(hwnd)
    if not rect or not user32.IsWindowVisible(hwnd) or auto_hide():
        return None
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    if width <= height * 3:
        return None  # a taskbar on the side of the screen has no room for a wide block
    _, _, scale = fl.monitor_at((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
    notify = window_rect(user32.FindWindowExW(hwnd, None, "TrayNotifyWnd", None))
    if notify and notify[0] > rect[0] + width // 2:
        right = notify[0]
    else:  # other displays: no notification area, maybe a clock (a button, measured below)
        right = rect[2] - round((260 if key == "main" else 8) * scale)
    occupied, measured = None, False
    try:
        spans = _COM.buttons(hwnd)
        if spans:
            occupied = [(a, b) for a, b, top, bottom in spans
                        if b - a < width * .4 and bottom - top >= height * .4 and a < right and b > rect[0]
                        and top >= rect[1] - 2 and bottom <= rect[3] + 2]
            measured = bool(occupied)
    except Exception:
        log.warning("taskbar buttons could not be read", exc_info=True)
    if not measured:
        # Estimate: Windows 11 centres the buttons (widgets on the far left), Windows 10 starts them on the left.
        centred = setting("TaskbarAl", 1) == 1
        mid = (rect[0] + rect[2]) // 2
        occupied = [(mid - width // 4, mid + width // 4)] if centred else [(rect[0], rect[0] + width // 2)]
        if centred and setting("TaskbarDa", 1) and key == "main":
            occupied.append((rect[0], rect[0] + round(190 * scale)))
        if key != "main":
            occupied.append((rect[2] - round(130 * scale), rect[2]))  # its clock
    return Bar(hwnd, key, label, rect, scale, rect[0] + round(8 * scale), right - round(12 * scale), occupied,
               light_taskbar(), measured)


class TaskbarBlock(fl.Popup):
    """One provider's block on the taskbar."""
    modal = False            # other popups ignore it; it never closes on outside clicks
    take_focus = False
    dismiss_on_deactivate = False
    ex_style = WS_EX_NOACTIVATE
    FX_SECONDS = {**fl.Popup.FX_SECONDS, "width": 0.32}

    def __init__(self, view, provider):
        super().__init__(view.tray)
        self.view, self.provider = view, provider
        self.spot = None  # (edge x, "left" | "right", columns) in physical px
        self.bar = None

    def on_panel(self, x, y):
        return True  # no shadow margin: the whole image is the block

    def contains(self, point):
        return self.hwnd is not None and self.x <= point[0] < self.x + self.size[0] and self.y <= point[1] < self.y + self.size[1]

    def fx_targets(self):
        state, bar = self.tray.state, self.bar
        fx = {key: value for key, value in fr.targets(state).items() if key[0] in ("active", "bar")}
        if self.hover:
            fx[("hover", self.hover)] = 1.0
        fx[("width",)] = fr.block_width(state, self.provider, bar.height, bar.light, self.spot[2]) or 120
        return fx

    def render(self, hover):
        bar = self.bar
        return fr.render_block(self.tray.state, self.provider, hover, self.scale, self.fx, bar.height, bar.light,
                               self.fx.get(("width",)), self.spot[2])

    def place(self, bar, spot):
        self.bar, self.spot, self.scale = bar, spot, bar.scale

    def position(self, width, height):
        edge, side, _ = self.spot
        x = edge if side == "left" else edge - width
        rect = self.bar.rect
        y = rect[1] + (rect[3] - rect[1] - height) // 2
        return x, y, (0, -1)  # opens rising out of the taskbar's bottom edge

    def redraw(self, retarget=True):
        if not self.hwnd or self.closing:
            return
        if retarget:
            self.retarget()
        image, self.hits = self.render(self.hover)
        self.size = image.size
        if not self.anim:
            self.x, self.y, _ = self.position(*image.size)
        self._push(image, self.alpha)

    @property
    def owner(self):
        # Owned by the taskbar: Windows keeps it just above the taskbar in one step whenever the
        # taskbar comes forward (clicks, focus changes), so it never drops behind and back.
        return self.bar.hwnd if self.bar else None

    def wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_MOUSEACTIVATE:
            return MA_NOACTIVATE
        if msg == WM_DESTROY and self.hwnd == hwnd:  # its taskbar went away (Explorer restarted)
            fl.Popup._windows.pop(hwnd, None)
            self.hwnd, self.anim, self.fx_anims, self.closing = None, None, {}, False
            self.view.later()
            return 0
        if msg == WM_RBUTTONUP:
            self.view.open_menu()
            return 0
        return super().wndproc(hwnd, msg, wparam, lparam)

    def activate(self, action):
        if action == "open":
            self.view.open_panel(self)


class TaskbarView:
    """Keeps a block per provider in use on the taskbar. Everything here runs on the tray thread;
    other threads call post()."""

    def __init__(self, tray):
        self.tray = tray
        self.blocks = {}
        self.bar = None
        self.stale = True        # re-read the taskbar's layout on the next sync
        self.hwnd = None
        self.hooked = False
        self.shell_message = None

    # ---------- wiring into pystray's hidden window ----------
    def attach(self, icon):
        """Add our messages to pystray's window procedure (before the icon runs)."""
        self.icon = icon
        handlers = icon._message_handlers
        handlers[WM_APP_TASKBAR] = lambda w, l: self.sync()
        handlers[WM_TIMER] = self.on_timer
        handlers[WM_SETTINGCHANGE] = lambda w, l: self.later()
        for message in (WM_DISPLAYCHANGE, fl_taskbar_created()):
            previous = handlers.get(message)
            handlers[message] = self._chain(previous)
        self.shell_message = user32.RegisterWindowMessageW("SHELLHOOK")
        handlers[self.shell_message] = self.on_shell

    def _chain(self, previous):
        def handler(wparam, lparam):
            if previous:
                previous(wparam, lparam)
            self.later()
        return handler

    def post(self):
        """Any thread: sync with the latest state on the tray thread."""
        hwnd = getattr(getattr(self, "icon", None), "_hwnd", None)
        if hwnd:
            user32.PostMessageW(hwnd, WM_APP_TASKBAR, 0, 0)

    def later(self, ms=400):
        """Re-read the taskbar a moment from now (after its own buttons have finished moving)."""
        self.stale = True
        if self.hwnd:
            user32.SetTimer(self.hwnd, TIMER_LAYOUT, ms, None)

    def hook(self):
        if self.hooked:
            return
        self.hwnd = self.icon._hwnd
        user32.RegisterShellHookWindow(self.hwnd)
        user32.SetTimer(self.hwnd, TIMER_MINUTE, 60_000, None)
        self.hooked = True

    def unhook(self):
        if not self.hooked:
            return
        user32.DeregisterShellHookWindow(self.hwnd)
        user32.KillTimer(self.hwnd, TIMER_MINUTE)
        user32.KillTimer(self.hwnd, TIMER_LAYOUT)
        self.hooked = False

    # ---------- events ----------
    def on_timer(self, wparam, lparam):
        if wparam == TIMER_LAYOUT:
            user32.KillTimer(self.hwnd, TIMER_LAYOUT)
            self.sync()
        elif wparam == TIMER_MINUTE:  # reset times move on; the taskbar may have changed too
            self.stale = True
            self.sync()

    def on_shell(self, wparam, lparam):
        if wparam & 0x7FFF in (HSHELL_WINDOWCREATED, HSHELL_WINDOWDESTROYED):
            self.later()  # a taskbar button came or went: the free space moved

    # ---------- layout ----------
    def enabled(self):
        return bool(self.tray.state.get("taskbar", True)) and not self.tray.quitting

    def sync(self):
        try:
            self._sync()
        except Exception:
            log.exception("taskbar view update failed")

    def _sync(self):
        if not self.enabled():
            self.close_all()
            self.unhook()
            return
        self.hook()
        state = self.tray.state
        wanted_key = state.get("taskbarDisplay") or "main"
        if wanted_key != getattr(self, "wanted_key", None):  # moved to another display in the settings
            self.wanted_key, self.stale = wanted_key, True
        if self.stale or self.bar is None:
            found = taskbars()
            displays = [{"id": key, "label": label} for _, key, label in found]
            if displays != getattr(self.tray.controller, "taskbar_displays", None):
                self.tray.controller.taskbar_displays = displays  # the settings list them
                self.tray.controller.notify("changed", None)
            # The chosen display's taskbar, else the main one; never some other display by accident.
            chosen = next((t for t in found if t[1] == wanted_key), None) or next((t for t in found if t[1] == "main"), None)
            self.bar, self.stale = (read_bar(*chosen) if chosen else None), False
        bar = self.bar
        in_use = [p for p in PROVIDER_ORDER if any(a["provider"] == p and a["active"] for a in state["accounts"])]
        if bar is None or not in_use:
            self.close_all()
            return
        wanted = []
        for provider in in_use:
            account = next(a for a in state["accounts"] if a["provider"] == provider and a["active"])
            wanted.append((provider, {c: math.ceil(fr.block_width(state, provider, bar.height, bar.light, c) * bar.scale)
                                      for c in range(min(3, max(1, len(account["windows"]))), 0, -1)},
                           "left" if provider == "claude" else "right"))  # Claude from the left, Codex from the right
        spots = place_blocks(free_gaps(bar.left, bar.right, bar.occupied, round(12 * bar.scale)), wanted,
                             round(12 * bar.scale))
        for provider in PROVIDER_ORDER:
            block = self.blocks.get(provider)
            if provider not in spots:
                if block:
                    block.close()
                continue
            x, width, columns = spots[provider]
            side = "left" if provider == "claude" else "right"
            spot = (x if side == "left" else x + width, side, columns)
            if block is None:
                block = self.blocks[provider] = TaskbarBlock(self, provider)
            if block.hwnd and block.bar is not None and block.bar.hwnd != bar.hwnd:
                block._destroy()  # moving to another display's taskbar: a new window owned by it
            block.place(bar, spot)
            if block.hwnd and not block.closing:
                block.redraw()
            else:
                block.open()

    def close_all(self):
        for block in self.blocks.values():
            block.close()

    def dismiss(self):
        for block in self.blocks.values():
            block.dismiss()

    # ---------- clicks ----------
    def open_panel(self, block):
        """The panel above the block, listing just that provider's accounts. A second click on
        the same block closes it; a click on the other block switches it over."""
        flyout = self.tray.flyout
        if flyout is None:
            return
        same = flyout.only == block.provider
        if same and flyout.hwnd and not flyout.closing:
            flyout.close()
            return
        if same and time.monotonic() < flyout.suppress_until:
            flyout.suppress_until = 0.0  # the press on this block already closed it
            return
        flyout.origin = ((block.x, self.bar.rect[1], block.x + block.size[0], self.bar.rect[3]), block.provider)
        flyout.open()

    def open_menu(self):
        menu = self.tray.menu
        if menu is not None:
            flyout = self.tray.flyout
            if flyout and not flyout.pinned:
                flyout.close()
            menu.toggle()


def fl_taskbar_created():
    return user32.RegisterWindowMessageW("TaskbarCreated")
