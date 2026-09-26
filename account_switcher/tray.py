"""Account Switcher: a notification-area icon is the whole resident app.

Left-click opens the dashboard (the local Web UI) in a borderless browser app window.
Right-click offers one-click swaps, the Auto swap / AFK switches and Quit.

Idle cost is kept near zero: no Tk, no timers, no polling. The tray's Win32 message
loop and one refresh thread both block until something happens; the icon image and
menu are rebuilt only when what they show actually changes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

import pystray
from PIL import Image, ImageDraw

from .web import Controller, clear_url_file, make_server, write_url_file

APP = "Account Switcher"
PROVIDERS = (("claude", "Claude"), ("codex", "Codex"))
ASSETS = Path(__file__).with_name("static") / "assets"
LEVEL_RGB = {"good": (76, 195, 138), "warn": (229, 181, 74), "bad": (239, 106, 91)}
_local = build_opener(ProxyHandler({}))  # loopback only; never through a system proxy


# ---------- pure presentation helpers (unit-tested) ----------
def remaining(used):
    return max(0, min(100, 100 - used))


def headroom(account):
    """Remaining percent of the tightest account-wide window."""
    return min(remaining(account["five_hour"]), remaining(account["weekly"]))


def level(left):
    return "good" if left > 30 else "warn" if left > 10 else "bad"


def short_name(account):
    return account["alias"].split(" · ")[-1].replace(" (synthetic)", "")


def active_accounts(state):
    return [a for a in state["accounts"] if a["active"]]


def tray_level(state):
    """Worst level across the accounts currently in use; drives the icon's status dot."""
    levels = [level(headroom(a)) if a["eligible"] else "bad" for a in active_accounts(state)]
    for name in ("bad", "warn", "good"):
        if name in levels:
            return name
    return None


def tooltip(state):
    lines = [APP]
    for provider, title in PROVIDERS:
        account = next((a for a in active_accounts(state) if a["provider"] == provider), None)
        if account:
            status = f"{headroom(account):.0f}% left" if account["eligible"] else "limit reached"
            lines.append(f"{title}: {short_name(account)} · {status}")
    modes = ["Auto swap" if state["autoSwap"] else "Manual"] + (["AFK"] if state["afk"] else [])
    lines.append(" · ".join(modes))
    return "\n".join(lines)[:127]  # Windows tooltip limit


def account_label(account):
    if not account["eligible"]:
        return f"{short_name(account)}  ·  limit reached"
    return (f"{short_name(account)}  ·  {remaining(account['five_hour']):.0f}% 5h"
            f"  ·  {remaining(account['weekly']):.0f}% week")


def menu_signature(state):
    """Everything the menu shows; the menu is rebuilt only when this changes."""
    return (state["autoSwap"], state["afk"], state["busy"],
            tuple((a["id"], a["active"], account_label(a)) for a in state["accounts"]))


# ---------- icon ----------
_icons = {}


def icon_image(status):
    """App mark with a small status dot. Built once per status and cached."""
    if status not in _icons:
        with Image.open(ASSETS / "switcher.png") as source:
            image = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
        if status:
            draw = ImageDraw.Draw(image)
            draw.ellipse((38, 38, 63, 63), fill=(22, 22, 22, 255))
            draw.ellipse((42, 42, 59, 59), fill=LEVEL_RGB[status] + (255,))
        _icons[status] = image
    return _icons[status]


# ---------- dashboard window ----------
def app_browser():
    """A Chromium browser that can open the dashboard as a plain app window."""
    if sys.platform != "win32":
        return None
    roots = [os.environ.get(k) for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for relative in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
        for root in filter(None, roots):
            path = Path(root) / relative
            if path.is_file():
                return str(path)
    return shutil.which("msedge")


def open_dashboard(url):
    browser = app_browser()
    if browser:
        # --app gives a window without tabs or address bar; it joins the browser's
        # existing process if one is running, and all of it goes away when closed.
        subprocess.Popen([browser, f"--app={url}", "--window-size=1200,900"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    else:
        webbrowser.open(url)


def existing_instance(url_file):
    """Launch URL of an already-running copy, or None."""
    try:
        url = Path(url_file).read_text(encoding="utf-8").strip()
        base, token = url.split("/#token=")
        request = Request(base + "/api/state?after=-1", headers={"Authorization": "Bearer " + token})
        with _local.open(request, timeout=2) as response:
            json.load(response)
        return url
    except (OSError, ValueError):
        return None


# ---------- tray host ----------
class Tray:
    def __init__(self, controller, server, icon_factory=pystray.Icon):
        self.controller, self.server = controller, server
        self.url = server.launch_url
        self.state = controller.snapshot()
        self.shown = {"title": None, "level": None, "menu": None}
        self.last_active = {a["provider"]: a["id"] for a in active_accounts(self.state)}
        self.quitting = False
        self.icon = icon_factory("account-switcher", icon_image(tray_level(self.state)),
                                 tooltip(self.state), pystray.Menu(self.menu_items))
        # The dashboard's "Shut down" button quits the tray too.
        server.quit = self.quit

    # Menu is generated from the latest snapshot each time pystray rebuilds it.
    def menu_items(self):
        state = self.state
        yield pystray.MenuItem(f"Open {APP}", lambda: open_dashboard(self.url), default=True)
        for provider, title in PROVIDERS:
            accounts = [a for a in state["accounts"] if a["provider"] == provider]
            if not accounts:
                continue
            yield pystray.Menu.SEPARATOR
            yield pystray.MenuItem(title, None, enabled=False)
            for account in accounts:
                yield pystray.MenuItem(
                    account_label(account), self.swapper(account["id"]),
                    checked=lambda _, active=account["active"]: active, radio=True,
                    enabled=account["eligible"] and not state["busy"])
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Auto swap", self.toggle("autoSwap"),
                               checked=lambda _: state["autoSwap"], enabled=not state["busy"])
        yield pystray.MenuItem("AFK mode", self.toggle("afk"),
                               checked=lambda _: state["afk"], enabled=not state["busy"])
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Quit", self.quit)

    def swapper(self, account_id):
        def swap():
            if not next(a for a in self.state["accounts"] if a["id"] == account_id)["active"]:
                self.act("swap", {"id": account_id})
        return swap

    def toggle(self, key):
        def flip():
            prefs = {"autoSwap": self.state["autoSwap"], "afk": self.state["afk"]}
            prefs[key] = not prefs[key]
            self.act("preferences", prefs)
        return flip

    def act(self, action, body):
        try:
            self.controller.action(action, body)
        except (RuntimeError, ValueError) as error:
            self.icon.notify(str(error), APP)

    def refresh(self):
        """Push the latest state to the icon, touching only what changed."""
        state = self.state = self.controller.snapshot()
        title = tooltip(state)
        if title != self.shown["title"]:
            self.icon.title = self.shown["title"] = title
        status = tray_level(state)
        if status != self.shown["level"]:
            self.icon.icon = icon_image(status)
            self.shown["level"] = status
        signature = menu_signature(state)
        if signature != self.shown["menu"]:
            self.shown["menu"] = signature
            self.icon.update_menu()
        self.announce_failovers(state)

    def announce_failovers(self, state):
        for account in active_accounts(state):
            provider, previous = account["provider"], self.last_active.get(account["provider"])
            self.last_active[provider] = account["id"]
            if previous is None or previous == account["id"]:
                continue
            if account["id"] in self.controller.manual_swaps:
                self.controller.manual_swaps.discard(account["id"])
                continue
            old = next((a for a in state["accounts"] if a["id"] == previous), None)
            reason = f"{short_name(old)} hit its limit. " if old and not old["eligible"] else ""
            self.icon.notify(f"{reason}Now using {short_name(account)}.", f"{dict(PROVIDERS)[provider]} switched accounts")

    def watch(self):
        """Block until the controller changes, refresh, repeat. No timeout, so no idle wake-ups."""
        seen = self.state["revision"]
        condition = self.controller.condition
        while True:
            with condition:
                condition.wait_for(lambda: self.controller.revision != seen or self.controller.closed)
                if self.controller.closed:
                    break
            time.sleep(.15)  # coalesce bursts such as streamed text into one refresh
            seen = self.controller.revision
            try:
                self.refresh()
            except Exception:  # never let a display hiccup kill the watcher
                pass
        self.quit()

    def quit(self, *_):
        if not self.quitting:
            self.quitting = True
            self.icon.stop()

    def run(self, open_now=False):
        def setup(icon):
            icon.visible = True
            self.refresh()
            threading.Thread(target=self.watch, daemon=True).start()
            if open_now:
                open_dashboard(self.url)
        self.icon.run(setup=setup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--simulator", action="store_true", help="Lightweight routing simulation instead of the compiled proxy")
    parser.add_argument("--url-file", help="Where the private dashboard URL is kept while running")
    parser.add_argument("--quiet", action="store_true", help="Start in the tray without opening the dashboard")
    args = parser.parse_args()

    if args.url_file:
        running = existing_instance(args.url_file)
        if running:  # second launch: just bring up the dashboard of the running copy
            open_dashboard(running)
            return

    controller = Controller(args.simulator)
    server = make_server(controller, args.port, idle_seconds=0)
    write_url_file(args.url_file, server.launch_url)
    # A long poll interval: the loop never has to exit on its own because quitting
    # goes through the tray, so this thread just sleeps between connections.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 60}, daemon=True).start()
    try:
        Tray(controller, server).run(open_now=not args.quiet)
    finally:
        controller.close()  # stops any proxy / Claude processes this app owns
        clear_url_file(args.url_file, server.launch_url)


if __name__ == "__main__":
    main()
