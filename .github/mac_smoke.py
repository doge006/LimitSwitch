"""macOS smoke test on a real Mac (GitHub Actions): the installed app shows its menu bar icon,
opens its window, and quits cleanly (Codex config restored). Screenshots go to ./shots."""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

import Quartz

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "shots"
SHOTS.mkdir(exist_ok=True)
failures = []


def check(ok, text):
    print(("PASS " if ok else "FAIL ") + text, flush=True)
    if not ok:
        failures.append(text)


def app_pid():
    done = subprocess.run(["pgrep", "-f", "AccountSwitcher.pyw"], capture_output=True, text=True)
    pids = [int(p) for p in done.stdout.split()]
    return pids[0] if pids else None


def windows(pid):
    info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID) or []
    found = []
    for w in info:
        if w.get("kCGWindowOwnerPID") == pid:
            b = w.get("kCGWindowBounds", {})
            found.append({"layer": w.get("kCGWindowLayer"), "onscreen": bool(w.get("kCGWindowIsOnscreen")),
                          "name": w.get("kCGWindowName"), "owner": w.get("kCGWindowOwnerName"),
                          "x": b.get("X"), "y": b.get("Y"), "w": b.get("Width"), "h": b.get("Height")})
    return found


def shot(name, region=None):
    args = ["screencapture", "-x"] + (["-R" + ",".join(map(str, region))] if region else []) + [str(SHOTS / name)]
    subprocess.run(args, check=False)


def api(path):
    url = (ROOT / ".runtime" / "tray.url").read_text().strip()
    base, token = url.split("/#token=")
    request = Request(base + path, data=b"{}", method="POST",
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


def click(x, y):
    for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        event = Quartz.CGEventCreateMouseEvent(None, kind, (x, y), Quartz.kCGMouseButtonLeft)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        time.sleep(.1)


pid = app_pid()
check(pid is not None, "app process is running")
if pid is None:
    sys.exit(1)
time.sleep(3)
shot("1-desktop.png")
found = windows(pid)
print(json.dumps(found, indent=1))
status = [w for w in found if w["layer"] == Quartz.kCGStatusWindowLevel]
check(bool(status), "status item window exists")
check(any(w["onscreen"] and (w["w"] or 0) > 0 for w in status), "status item is on screen with a width")
opened_by_install = [w for w in found if w["layer"] == 0 and w["onscreen"]]
check(bool(opened_by_install), "opening the app shows its window")

if status:
    item = status[0]
    shot("2-menubar.png", (max(0, int(item["x"]) - 300), 0, 600, 40))
    click(item["x"] + item["w"] / 2, item["y"] + item["h"] / 2)
    time.sleep(2)
    shot("3-after-click.png")
    after = windows(pid)
    print(json.dumps(after, indent=1))
    popover = [w for w in after if w["onscreen"] and w["layer"] not in (0, Quartz.kCGStatusWindowLevel)
               and (w["h"] or 0) > 100]
    print("popover opened by click:", bool(popover), "(needs event-posting permission; informational)")

print("show:", api("/api/show"))
time.sleep(2)
shot("4-window.png")
check(any(w["layer"] == 0 and w["onscreen"] for w in windows(pid)), "the app window is on screen after /api/show")

config = Path.home() / ".codex" / "config.toml"
check("account-switcher" in config.read_text() if config.exists() else False, "Codex is routed while running")
api("/api/shutdown")
for _ in range(40):
    if app_pid() is None:
        break
    time.sleep(.25)
check(app_pid() is None, "quit ends the process")
check(not config.exists() or "account-switcher" not in config.read_text(), "quit restores the Codex config")
check(not (ROOT / ".runtime" / "tray.url").exists(), "quit removes the runtime URL")
log = Path.home() / "Library" / "Application Support" / "AccountSwitcher" / "app.log"
print("---- app.log ----")
print(log.read_text() if log.exists() else "(none)")
sys.exit(1 if failures else 0)
