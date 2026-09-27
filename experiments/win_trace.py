"""Windows CI experiment: log every change to Edge top-level windows while the full view opens,
to see exactly which windows appear, hide, show and close. Run it on Windows from the repo root,
after the installer (it starts and quits the app itself)."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
URL_FILE = ROOT / ".runtime" / "tray.url"
user32 = ctypes.windll.user32
T0 = time.monotonic()


def windows():
    found = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        cls, title = ctypes.create_unicode_buffer(64), ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value == "Chrome_WidgetWin_1":
            user32.GetWindowTextW(hwnd, title, 256)
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if title.value or user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found[hwnd] = (bool(user32.IsWindowVisible(hwnd)), title.value[:40],
                               (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top), pid.value)
        return True

    user32.EnumWindows(each, 0)
    return found


def trace(label, seconds, action=None):
    print(f"==== {label}", flush=True)
    last = windows()
    for hwnd, info in last.items():
        print(f"  start {hwnd:#x} {info}", flush=True)
    if action:
        threading.Thread(target=action, daemon=True).start()
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        now = windows()
        t = f"{(time.monotonic() - start) * 1000:7.0f}ms"
        for hwnd in now.keys() - last.keys():
            print(f"{t} NEW  {hwnd:#x} {now[hwnd]}", flush=True)
        for hwnd in last.keys() - now.keys():
            print(f"{t} GONE {hwnd:#x} {last[hwnd]}", flush=True)
        for hwnd in now.keys() & last.keys():
            if now[hwnd] != last[hwnd]:
                print(f"{t} CHG  {hwnd:#x} {now[hwnd]}", flush=True)
        last = now
        time.sleep(0.004)


def api(path):
    base, token = URL_FILE.read_text().strip().split("/#token=")
    request = Request(base + path, data=b"{}", method="POST",
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


pyw = str(ROOT / ".venv" / "Scripts" / "pythonw.exe")
edge = next(p for p in (Path(os.environ.get(k, "")) / r"Microsoft\Edge\Application\msedge.exe"
                        for k in ("ProgramFiles(x86)", "ProgramFiles")) if p.is_file())


def full_views():
    return [h for h, i in windows().items() if i[0] and i[1].startswith("Account Switcher")]


# A: Edge not running, the app opens its full view at start (Launch.cmd)
trace("A: start with the full view, Edge not running", 12,
      lambda: subprocess.Popen([pyw, "AccountSwitcher.pyw", "--show"], cwd=ROOT))
# B: full view open; open it again (tray / Start menu)
trace("B: open again while open", 4, lambda: api("/api/show"))
# C: close it, open it again (tray) with Edge's own window from A's process gone
for hwnd in full_views():
    user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
time.sleep(3)
trace("C: open from the tray after closing it", 8, lambda: api("/api/show"))
for hwnd in full_views():
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
time.sleep(3)
# D: the user's own Edge is running (a normal browser window), then open from the tray
subprocess.Popen([edge, "--no-first-run", "about:blank"])
time.sleep(6)
trace("D: open from the tray while Edge runs as a browser", 8, lambda: api("/api/show"))
for hwnd in full_views():
    user32.PostMessageW(hwnd, 0x0010, 0, 0)
time.sleep(2)
# E: second launch (Start menu) while running, no full view open
trace("E: Start menu launch while running", 10,
      lambda: subprocess.Popen([pyw, "AccountSwitcher.pyw"], cwd=ROOT))
api("/api/shutdown")
time.sleep(2)
subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
time.sleep(2)
