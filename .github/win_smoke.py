"""Windows smoke test on a real Windows machine (GitHub Actions): the app starts, its full view
opens with Account Switcher's own taskbar identity (not Edge's), and quitting cleans up; then,
in demo mode, the taskbar view shows a block per provider and animates a swap.
Screenshots go to ./shots."""
import ctypes
import json
from pathlib import Path
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

from PIL import ImageGrab

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from account_switcher import win_window  # noqa: E402

SHOTS = ROOT / "shots"
SHOTS.mkdir(exist_ok=True)
failures = []


def check(ok, text):
    print(("PASS " if ok else "FAIL ") + text, flush=True)
    if not ok:
        failures.append(text)


def api(path, body=None):
    base, token = (ROOT / ".runtime" / "tray.url").read_text().strip().split("/#token=")
    request = Request(base + path, data=json.dumps(body or {}).encode(), method="POST",
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


for _ in range(120):
    if (ROOT / ".runtime" / "tray.url").exists():
        break
    time.sleep(0.25)
check((ROOT / ".runtime" / "tray.url").exists(), "the app started")
windows = []
for _ in range(80):
    windows = win_window.find_windows()
    if windows and win_window.get_identity(windows[0]) == win_window.APP_ID:
        break
    time.sleep(0.25)
check(bool(windows), "the full view opened in its own window")
if windows:
    check(win_window.get_identity(windows[0]) == win_window.APP_ID, "the window has Account Switcher's taskbar identity")
    time.sleep(1)
    rect = (ctypes.c_long * 4)()
    ctypes.windll.user32.GetWindowRect(windows[0], rect)
    print("full view size:", rect[2] - rect[0], "x", rect[3] - rect[1], flush=True)
    check(rect[3] - rect[1] <= 700, "the full view opens at its own size (90% of this short screen), not a remembered one")
time.sleep(2)
ImageGrab.grab().save(SHOTS / "1-full-view.png")
config = Path.home() / ".codex" / "config.toml"
check(config.exists() and "account-switcher" in config.read_text(), "Codex is routed while running")
api("/api/shutdown")
for _ in range(40):
    if not (ROOT / ".runtime" / "tray.url").exists():
        break
    time.sleep(0.25)
check(not (ROOT / ".runtime" / "tray.url").exists(), "quit ends the app")
check(not config.exists() or "account-switcher" not in config.read_text(), "quit restores the Codex config")

# ---- Taskbar view: demo accounts are in use, so a block per provider sits on the taskbar ----
from ctypes import wintypes  # noqa: E402
import subprocess  # noqa: E402

from account_switcher import taskbar  # noqa: E402

user32 = ctypes.windll.user32


def blocks():
    """Visible Account Switcher popups sitting on the taskbar: [(left, top, right, bottom)]."""
    found = []
    bar = taskbar.window_rect(user32.FindWindowW("Shell_TrayWnd", None))

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        rect = taskbar.window_rect(hwnd)
        if name.value == "AccountSwitcherFlyout" and user32.IsWindowVisible(hwnd) and rect and bar \
                and rect[1] >= bar[1] - 2 and rect[3] <= bar[3] + 2:
            found.append(rect)
        return True

    user32.EnumWindows(each, 0)
    return sorted(found)


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD), ("dmDriverVersion", wintypes.WORD),
                ("dmSize", wintypes.WORD), ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG), ("dmDisplayOrientation", wintypes.DWORD),
                ("dmDisplayFixedOutput", wintypes.DWORD), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                ("dmFormName", wintypes.WCHAR * 32), ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD), ("dmDisplayFlags", wintypes.DWORD),
                ("dmDisplayFrequency", wintypes.DWORD), ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD), ("dmReserved1", wintypes.DWORD),
                ("dmReserved2", wintypes.DWORD), ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


# The runner's screen is 1024 px wide, too narrow for the blocks beside its taskbar buttons: use a
# typical 1920 x 1080 (the app itself would simply not show a block that does not fit).
mode = DEVMODEW(dmSize=ctypes.sizeof(DEVMODEW), dmFields=0x80000 | 0x100000, dmPelsWidth=1920, dmPelsHeight=1080)
print("display 1920x1080:", user32.ChangeDisplaySettingsW(ctypes.byref(mode), 0), flush=True)  # 0 = changed
time.sleep(3)
info = taskbar.read_bar(*taskbar.taskbars()[0])
print("taskbar:", info and {"rect": info.rect, "scale": info.scale, "free from": info.left, "to": info.right,
                            "buttons": sorted(info.occupied), "measured": info.measured, "light": info.light}, flush=True)
subprocess.Popen([str(ROOT / ".venv" / "Scripts" / "pythonw.exe"), "AccountSwitcher.pyw", "--demo"], cwd=ROOT)
for _ in range(120):
    if (ROOT / ".runtime" / "tray.url").exists():
        break
    time.sleep(0.25)
found = []
for _ in range(40):
    found = blocks()
    if len(found) >= 2:
        break
    time.sleep(0.25)
time.sleep(1)  # let the rise-in animation finish before measuring
found = blocks()
print("blocks:", found, flush=True)
check(len(found) == 2, "a block for Claude and one for Codex sit on the taskbar")
if found:
    check(all(r[3] - r[1] <= (info.rect[3] - info.rect[1] if info else 99) for r in found), "the blocks fit inside the taskbar")
    if len(found) == 2:
        check(found[0][2] <= found[1][0], "the blocks do not overlap")
    for a, b, *_ in (info.occupied if info and info.measured else []):
        check(not any(r[0] < b and a < r[2] for r in found), f"no block covers the taskbar button at {a}-{b}")
    owner = user32.FindWindowW("Shell_TrayWnd", None)
    owned = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def owned_by_taskbar(hwnd, _):
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value == "AccountSwitcherFlyout" and user32.IsWindowVisible(hwnd) and user32.GetWindow(hwnd, 4) == owner:
            owned.append(hwnd)  # GW_OWNER: kept above the taskbar by Windows, no re-raising (no flicker)
        return True

    user32.EnumWindows(owned_by_taskbar, 0)
    check(len(owned) == len(found), "the blocks are owned by the taskbar")
    user32.SetForegroundWindow(owner)  # focus moves to the taskbar and back: the blocks stay put
    time.sleep(0.3)
    check(blocks() == found, "the blocks stay put when focus changes")
time.sleep(1)
screen = ImageGrab.grab()
height = info.rect[3] - info.rect[1] if info else 48
screen.crop((0, screen.height - height - 40, screen.width, screen.height)).save(SHOTS / "2-taskbar.png")
api("/api/swap", {"id": "codex-b"})  # the Codex block slides the old account out and the new one in
time.sleep(0.2)
ImageGrab.grab().crop((0, screen.height - height - 40, screen.width, screen.height)).save(SHOTS / "3-taskbar-swapping.png")
time.sleep(1)
ImageGrab.grab().crop((0, screen.height - height - 40, screen.width, screen.height)).save(SHOTS / "4-taskbar-swapped.png")
if len(found) == 2:  # a click on the Codex block opens the panel with only the Codex accounts
    right = found[1]
    user32.SetCursorPos((right[0] + right[2]) // 2, (right[1] + right[3]) // 2)
    user32.mouse_event(2, 0, 0, 0, 0)
    user32.mouse_event(4, 0, 0, 0, 0)
    time.sleep(1)
    ImageGrab.grab().crop((screen.width // 2, screen.height // 2, screen.width, screen.height)).save(SHOTS / "5-codex-panel.png")
    user32.SetCursorPos(10, 10)
if found:  # a full-screen window (a game, a video) covers the taskbar, and the blocks go down with it
    msg = wintypes.MSG()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.02)

    user32.CreateWindowExW.restype = wintypes.HWND
    full = user32.CreateWindowExW(0, "Static", "Full screen", 0x80000000 | 0x10000000, 0, 0,
                                  screen.width, screen.height, None, None, None, None)
    user32.keybd_event(0x12, 0, 0, 0)  # an Alt tap lets this process take the foreground
    user32.SetForegroundWindow(full)
    user32.keybd_event(0x12, 0, 2, 0)
    pump(2)
    print("full-screen window in front:", user32.GetForegroundWindow() == full, "· taskbar on top:",
          bool(user32.GetWindowLongW(owner, -20) & 0x8), flush=True)
    ImageGrab.grab().crop((0, screen.height - height - 40, screen.width, screen.height)).save(SHOTS / "6-under-full-screen.png")
    check(not blocks(), "the blocks go down with the taskbar under a full-screen window")
    user32.DestroyWindow(full)
    back = []
    for _ in range(12):
        pump(0.5)
        back = blocks()
        if len(back) == len(found) and back[0][0] == found[0][0] and back[-1][2] == found[-1][2]:
            break
    print("after full screen:", back, "· taskbar on top:", bool(user32.GetWindowLongW(owner, -20) & 0x8), flush=True)
    # Claude is anchored by its left edge, Codex by its right (its width changed with the swap above).
    check(len(back) == len(found) and back[0][0] == found[0][0] and back[-1][2] == found[-1][2],
          "the blocks are back, in place, when it closes")
api("/api/taskbar", {"on": False})  # the menu's "Taskbar view" switch
gone = found
for _ in range(20):
    gone = blocks()
    if not gone:
        break
    time.sleep(0.25)
check(not gone, "turning the taskbar view off removes the blocks")
api("/api/shutdown")
time.sleep(2)
log = Path.home() / "AppData" / "Local" / "AccountSwitcher" / "app.log"
print("---- app.log ----\n" + (log.read_text() if log.exists() else "(none)"))
sys.exit(1 if failures else 0)
