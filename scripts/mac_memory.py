"""Where LimitSwitcher's memory goes on this Mac, step by step (a development tool).

Run it with the installed app's own Python, while the app itself may keep running:
    /Applications/LimitSwitcher.app/Contents/Resources/runtime/bin/python3 -B scripts/mac_memory.py
or straight from GitHub:
    curl -fsSL https://raw.githubusercontent.com/doge006/LimitSwitch/main/scripts/mac_memory.py | \\
        /Applications/LimitSwitcher.app/Contents/Resources/runtime/bin/python3 -B -

It loads what the app loads, in the same order, in demo mode (no accounts are touched), and
prints this process's footprint (what Activity Monitor shows) after each step.
"""
import gc
import os
import re
import subprocess
import sys

APP = "/Applications/LimitSwitcher.app/Contents/Resources/app"
if os.path.isdir(APP):
    sys.path.insert(0, APP)
sys.dont_write_bytecode = True
os.environ.setdefault("ACCOUNT_SWITCHER_HOME", "/tmp/limitswitcher-memory-test")  # nothing of the real app's

last = [0.0]


def footprint():
    out = subprocess.run(["footprint", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    m = re.search(r"Footprint:\s+([\d.]+)\s*(KB|MB|GB)", out)
    if not m:
        return 0.0
    value = float(m.group(1))
    return value / 1024 if m.group(2) == "KB" else value * 1024 if m.group(2) == "GB" else value


def relieve():
    import ctypes
    gc.collect()
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    libc.malloc_zone_pressure_relief.argtypes = (ctypes.c_void_p, ctypes.c_size_t)
    libc.malloc_zone_pressure_relief(None, 0)


def step(name):
    now = footprint()
    print(f"{now:7.1f} MB  {now - last[0]:+7.1f}  {name}", flush=True)
    last[0] = now


step("Python itself")
import objc  # noqa: E402,F401
import Foundation  # noqa: E402,F401
import AppKit  # noqa: E402,F401
step("PyObjC: AppKit + Foundation")
import WebKit  # noqa: E402,F401
step("PyObjC: WebKit (the panel)")
from account_switcher import tray, web, live, providers, macos_app  # noqa: E402,F401
step("the app's code")
print(f"  = {last[0]:.1f} MB: the menu bar app loads this much (Pillow loaded: {'PIL' in sys.modules})")
print("The full view runs in its own process, drawn natively: scripts/compare_native.py measures it.")
