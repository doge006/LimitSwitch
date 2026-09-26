"""Windows smoke test on a real Windows machine (GitHub Actions): the app starts, its full view
opens with Account Switcher's own taskbar identity (not Edge's), and quitting cleans up.
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


def api(path):
    base, token = (ROOT / ".runtime" / "tray.url").read_text().strip().split("/#token=")
    request = Request(base + path, data=b"{}", method="POST",
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
log = Path.home() / "AppData" / "Local" / "AccountSwitcher" / "app.log"
print("---- app.log ----\n" + (log.read_text() if log.exists() else "(none)"))
sys.exit(1 if failures else 0)
