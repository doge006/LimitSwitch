"""Windows smoke test on a real Windows machine (GitHub Actions): the app starts, its full view
opens with Account Switcher's own taskbar identity (not Edge's), and quitting cleans up; then,
in demo mode, the taskbar view shows a block per provider and animates a swap.
Screenshots go to ./shots."""
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
import ctypes  # noqa: E402
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
info = taskbar.read_bar()
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
print("blocks:", found, flush=True)
check(len(found) == 2, "a block for Claude and one for Codex sit on the taskbar")
if found:
    check(all(r[3] - r[1] <= (info.rect[3] - info.rect[1] if info else 99) for r in found), "the blocks fit inside the taskbar")
    if len(found) == 2:
        check(found[0][2] <= found[1][0], "the blocks do not overlap")
    for a, b, *_ in (info.occupied if info and info.measured else []):
        check(not any(r[0] < b and a < r[2] for r in found), f"no block covers the taskbar button at {a}-{b}")
time.sleep(1)
screen = ImageGrab.grab()
height = info.rect[3] - info.rect[1] if info else 48
screen.crop((0, screen.height - height - 40, screen.width, screen.height)).save(SHOTS / "2-taskbar.png")
api("/api/swap", {"id": "codex-b"})  # the Codex block slides the old account out and the new one in
time.sleep(0.2)
ImageGrab.grab().crop((0, screen.height - height - 40, screen.width, screen.height)).save(SHOTS / "3-taskbar-swapping.png")
time.sleep(1)
ImageGrab.grab().crop((0, screen.height - height - 40, screen.width, screen.height)).save(SHOTS / "4-taskbar-swapped.png")
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
