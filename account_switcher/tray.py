"""Account Switcher: a notification-area icon is the whole resident app.

Left-click shows a compact flyout (accounts, usage, one-click swap, Auto swap / AFK) with a
"Full view" button that opens the dashboard (the local Web UI) in a browser app window.
Right-click offers a short menu.

Idle cost is kept near zero: no Tk, no timers, no polling. The tray's Win32 message
loop and one refresh thread both block until something happens; the icon image and
menu are rebuilt only when what they show actually changes.
"""
import argparse
import json
import logging
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
    """Remaining percent of the tightest account-wide window (100 when not known yet)."""
    return account["headroom"] if account.get("headroom", -1) >= 0 else 100


def level(left):
    return "good" if left > 30 else "warn" if left > 10 else "bad"


def short_name(account):
    return account.get("name") or account["alias"]


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


def menu_signature(state):
    """Everything the menu shows; the menu is rebuilt only when this changes."""
    return state["autoSwap"], state["afk"], state["busy"]


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


def full_view_size():
    """Half the work area's width, and tall enough for two rows of cards (880, or 95% of a
    shorter screen), in the browser's own units so a scaled display gets the same share."""
    if sys.platform != "win32":
        return 1080, 800
    try:
        import ctypes
        from ctypes import wintypes
        area = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0)  # SPI_GETWORKAREA
        scale = ctypes.windll.user32.GetDpiForSystem() / 96 if hasattr(ctypes.windll.user32, "GetDpiForSystem") else 1
        width, height = (area.right - area.left) / scale, (area.bottom - area.top) / scale
        # Tall enough for two rows of account cards without scrolling, never taller than the screen.
        return max(720, round(width / 2)), max(480, min(880, round(height * .95)))
    except (OSError, AttributeError, ZeroDivisionError):
        return 1080, 800


def open_dashboard(url):
    browser = app_browser()
    if browser:
        # --app gives a window without tabs or address bar; it joins the browser's
        # existing process if one is running, and all of it goes away when closed.
        width, height = full_view_size()
        subprocess.Popen([browser, f"--app={url}", f"--window-size={width},{height}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        if sys.platform == "win32":  # its own taskbar button (name and icon), not Edge's
            try:
                from .integrations import launcher
                from .win_window import brand_full_view
                brand_full_view(launcher(), Path(__file__).with_name("static") / "assets" / "switcher.ico",
                                size=(width, height))
            except Exception:
                logging.getLogger("account_switcher").exception("full view taskbar identity")
    else:
        webbrowser.open(url)


def show_running(url):
    """Ask the running copy to show its own window; False when it has none (open a browser)."""
    try:
        base, token = url.split("/#token=")
        request = Request(base + "/api/show", data=b"{}", method="POST",
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with _local.open(request, timeout=2) as response:
            return bool(json.load(response).get("shown"))
    except (OSError, ValueError, AttributeError):
        return False


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


def _log_path():
    from .vault import data_dir
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "app.log"  # errors only; empty in normal use


# ---------- tray host ----------
class Tray:
    def __init__(self, controller, server, icon_factory=None, flyout=None):
        self.controller, self.server = controller, server
        self.url = server.launch_url
        self.state = controller.snapshot()
        self.shown = {"title": None, "level": None, "menu": None}
        self.last_active = {a["provider"]: a["id"] for a in active_accounts(self.state)}
        self.quitting = False
        native = icon_factory is None and sys.platform == "win32"
        if icon_factory is None:
            if native:
                from .flyout import tray_icon_class
                icon_factory = tray_icon_class()
            else:
                icon_factory = pystray.Icon
        self.icon = icon_factory("account-switcher", icon_image(tray_level(self.state)),
                                 tooltip(self.state), pystray.Menu(self.menu_items))
        # The dashboard's "Quit" button quits the tray too.
        server.quit = self.quit
        self.menu = self.taskbar = None
        if native:
            from .flyout import Flyout, TrayMenu
            flyout, self.menu = Flyout(self), TrayMenu(self)
            self.icon.popups = (flyout, self.menu)  # left/right clicks open our own popups
            try:  # the accounts in use, shown on the taskbar itself
                from .taskbar import TaskbarView
                self.taskbar = TaskbarView(self)
                self.taskbar.attach(self.icon)
                controller.taskbar_available = True
            except Exception:
                logging.getLogger("account_switcher").exception("taskbar view unavailable")
                self.taskbar = None
        self.flyout = flyout

    # Right-click menu; account swaps live in the flyout. Rebuilt only when it changes.
    def menu_items(self):
        state = self.state
        if self.flyout:
            yield pystray.MenuItem("Accounts", self.toggle_flyout, default=True)
            yield pystray.MenuItem("Full view", self.open_full_view)
        else:
            yield pystray.MenuItem("Full view", self.open_full_view, default=True)
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Auto swap", self.toggle("autoSwap"),
                               checked=lambda _: state["autoSwap"], enabled=not state["busy"])
        yield pystray.MenuItem("AFK mode", self.toggle("afk"),
                               checked=lambda _: state["afk"], enabled=not state["busy"])
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Quit", self.quit)

    def toggle_flyout(self):
        self.flyout.toggle()

    def open_full_view(self):
        open_dashboard(self.url)

    def popup_visible(self, shown):
        """Hide the tooltip while the panel or menu is open so it can't cover them."""
        self.popup_open = shown
        title = "" if shown else tooltip(self.state)
        if title != self.shown["title"]:
            self.icon.title = self.shown["title"] = title

    def poke(self):
        """Refresh usage soon if it is more than a minute old (panel opened)."""
        try:
            self.controller.action("refresh", {"ifOlderThan": 60})
        except (RuntimeError, ValueError):
            pass

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
        title = "" if getattr(self, "popup_open", False) else tooltip(state)
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
        for popup in (self.flyout, self.menu):
            if popup:
                popup.state_changed()
        if self.taskbar:
            self.taskbar.post()
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
            for popup in (self.flyout, self.menu):
                if popup:
                    popup.dismiss()
            if self.taskbar:
                self.taskbar.dismiss()
            self.icon.stop()

    def run(self, open_now=False):
        def setup(icon):
            icon.visible = True
            self.refresh()
            threading.Thread(target=self.watch, daemon=True).start()
            if open_now:
                open_dashboard(self.url)
        self.icon.run(setup=setup)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--demo", action="store_true", help="Synthetic accounts and the Recovery lab instead of your real logins")
    parser.add_argument("--simulator", action="store_true", help="Demo mode with a lightweight routing simulation instead of the compiled proxy")
    parser.add_argument("--url-file", help="Where the private dashboard URL is kept while running")
    parser.add_argument("--quiet", action="store_true", help="Start in the tray without opening the dashboard")
    parser.add_argument("--show", action="store_true", help="Open the dashboard even with --quiet (opened by the user)")
    args = parser.parse_args(argv)

    if args.url_file:
        running = existing_instance(args.url_file)
        if running:  # second launch: just bring up the dashboard of the running copy
            if not show_running(running):
                open_dashboard(running)
            return

    if sys.platform == "win32":
        from .flyout import enable_dpi_awareness
        enable_dpi_awareness()
    if sys.platform in ("win32", "darwin"):
        logging.basicConfig(filename=str(_log_path()), level=logging.WARNING,
                            format="%(asctime)s %(name)s %(levelname)s %(message)s")
    controller = Controller(args.simulator, live=not (args.demo or args.simulator))
    server = make_server(controller, args.port, idle_seconds=0)
    write_url_file(args.url_file, server.launch_url)
    # A long poll interval: the loop never has to exit on its own because quitting
    # goes through the tray, so this thread just sleeps between connections.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 60}, daemon=True).start()
    integrations = None
    if controller.live:  # route Codex through the app, and the Claude AFK hook
        from .integrations import Integrations
        integrations = Integrations(controller.gateway, server.hook_url, server.hook_token)
        controller.gateway.integrations = integrations
        try:
            integrations.start()
        except Exception:
            logging.getLogger("account_switcher").exception("integrations failed to start")
    done = []

    def shutdown():
        """Undo what the app set up. Runs once, from the host's quit or on the way out."""
        if done:
            return
        done.append(True)
        try:
            if integrations:
                integrations.stop()  # Codex and Claude Code keep working without the app
        finally:
            controller.close()  # stops any proxy / Claude processes this app owns
            clear_url_file(args.url_file, server.launch_url)

    try:
        if sys.platform == "darwin":
            from .macos_app import run  # menu bar app
            # Cocoa ends the process inside its own quit, so the app runs shutdown there.
            run(controller, server, open_now=args.show or not args.quiet, cleanup=shutdown)
        else:
            Tray(controller, server).run(open_now=args.show or not args.quiet)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
