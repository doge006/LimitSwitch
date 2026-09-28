"""The macOS full view in a process of its own, started by the menu bar app and ended when the
window closes.

The menu bar app runs all day, the full view a few minutes at a time. Drawing it needs Pillow,
fonts and window-sized frames, and on recent macOS the memory freed after closing stays counted
against the process that used it. Here it belongs to this short-lived process, and all of it goes
back to macOS when the process ends: the menu bar app never loads any of it.

It talks to the menu bar app through its local API (the same one the panel uses): the state by
long poll, actions by POST. The private URL comes in the environment, never on the command line.
"""
import json
import logging
import os
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ENV = "LIMITSWITCH_FULL_VIEW"  # the menu bar app's private URL: set, this process is the full view
APP = "LimitSwitcher"
log = logging.getLogger("account_switcher.fullview")
_local = build_opener(ProxyHandler({}))  # loopback only; never through a system proxy


class Remote:
    """What the full view uses of web.Controller, over the menu bar app's local API."""

    def __init__(self, url):
        base, token = url.split("/#token=")
        self.base, self.auth = base, "Bearer " + token

    def request(self, method, path, body=None, timeout=10):
        data = None if body is None else json.dumps(body).encode()
        request = Request(self.base + path, data=data, method=method,
                          headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with _local.open(request, timeout=timeout) as response:
                return json.loads(response.read() or b"null")
        except HTTPError as error:
            try:
                message = json.loads(error.read()).get("error") or str(error)
            except ValueError:
                message = str(error)
            raise (ValueError if error.code == 400 else RuntimeError)(message) from None
        except (URLError, OSError) as error:
            raise RuntimeError(f"{APP} isn't responding ({error})") from None

    def action(self, name, body):
        self.request("POST", "/api/" + name, body)

    def state(self, after=-1):
        return self.request("GET", f"/api/state?after={after}", timeout=40)  # the server answers within 20 s


def main():
    url = os.environ.pop(ENV)  # not passed on to anything this process starts
    remote = Remote(url)
    state = remote.state()

    import objc
    from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSColor,
                        NSEventModifierFlagCommand, NSEventModifierFlagOption, NSImage, NSMenu, NSMenuItem,
                        NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
                        NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
    from Foundation import NSMakeRect, NSMakeSize, NSObject
    from PyObjCTools import AppHelper

    from .fullview_mac import FullViewCanvas

    class FullViewApp(NSObject):
        def init(self):
            self = objc.super(FullViewApp, self).init()
            self.window = self.canvas = None
            return self

        def applicationDidFinishLaunching_(self, _note):
            NSApp.setMainMenu_(self.main_menu())
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)  # a Dock icon while it's open
            from pathlib import Path
            icon = Path(__file__).resolve().parent / "static" / "assets" / "appicon-mac.png"
            image = NSImage.alloc().initWithContentsOfFile_(str(icon))
            if image is not None:
                NSApp.setApplicationIconImage_(image)  # the Dock would show Python's icon otherwise
            self.open_window()
            threading.Thread(target=self.watch, args=(state["revision"],), daemon=True).start()
            if os.environ.get("LIMITSWITCH_PANEL_TEST"):  # CI: the smoke test reads this in app.log
                AppHelper.callLater(1, self.draw_test)

        @objc.python_method
        def open_window(self):
            # A real title bar above the drawing, dark to match it. At most a quarter of the
            # screen: half its width and half its height (plus 40 pt).
            from AppKit import NSAppearance, NSScreen
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                     | NSWindowStyleMaskResizable)
            area = NSScreen.mainScreen().visibleFrame().size if NSScreen.mainScreen() else NSMakeSize(1440, 900)
            width, height = max(640, area.width / 2), max(460, area.height / 2 + 40)
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, width, height), style, NSBackingStoreBuffered, False)
            window.setTitle_(APP)
            dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
            if dark is not None:
                window.setAppearance_(dark)
            window.setTitlebarAppearsTransparent_(True)
            window.setBackgroundColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.086, 0.086, 0.086, 1.0))
            window.setReleasedWhenClosed_(False)
            window.setMinSize_(NSMakeSize(600, 400))
            self.canvas = FullViewCanvas.alloc().initWithFrame_controller_state_(
                NSMakeRect(0, 0, width, height), remote, state)
            window.setContentView_(self.canvas)
            window.center()
            # Remembers the user's size from here on. A new name when the default size changes: the
            # old saved size would otherwise win over the new default.
            window.setFrameAutosaveName_("AccountSwitcherFullView.v3")
            window.setDelegate_(self)
            self.window = window
            NSApp.activateIgnoringOtherApps_(True)
            window.makeKeyAndOrderFront_(None)

        @objc.python_method
        def main_menu(self):
            """The standard app and Edit / Window menus: ⌘Q, ⌘W, copy and paste in the window."""
            bar = NSMenu.alloc().init()

            def submenu(title, items):
                holder = bar.addItemWithTitle_action_keyEquivalent_(title, None, "")
                menu = NSMenu.alloc().initWithTitle_(title)
                for entry in items:
                    if entry is None:
                        menu.addItem_(NSMenuItem.separatorItem())
                        continue
                    label, action, key, *rest = entry
                    item = menu.addItemWithTitle_action_keyEquivalent_(label, action, key)
                    if rest and rest[0] == "self":
                        item.setTarget_(self)
                    elif rest:
                        item.setKeyEquivalentModifierMask_(rest[0])
                holder.setSubmenu_(menu)
                return menu

            submenu(APP, [(f"About {APP}", "orderFrontStandardAboutPanel:", ""), None,
                          (f"Hide {APP}", "hide:", "h"),
                          ("Hide Others", "hideOtherApplications:", "h", NSEventModifierFlagCommand | NSEventModifierFlagOption),
                          None, (f"Quit {APP}", "quitAll:", "q", "self")])
            submenu("Edit", [("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), None, ("Cut", "cut:", "x"),
                             ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")])
            window = submenu("Window", [("Minimize", "performMiniaturize:", "m"), ("Close", "performClose:", "w")])
            NSApp.setWindowsMenu_(window)
            return bar

        def quitAll_(self, _sender):
            """⌘Q quits LimitSwitcher itself, as before, not just this window."""
            try:
                remote.request("POST", "/api/shutdown", {}, timeout=3)
            except (RuntimeError, ValueError):
                pass
            NSApp.terminate_(None)

        def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
            if self.window is not None:
                self.window.makeKeyAndOrderFront_(None)
            return False

        def windowWillClose_(self, _note):
            if self.canvas is not None:
                self.canvas.close()
                self.canvas = None
            AppHelper.callAfter(NSApp.terminate_, None)  # closing the window ends this process

        @objc.python_method
        def watch(self, seen):
            """Long-polls the menu bar app's state; ends this process when the app has gone."""
            while True:
                try:
                    new = remote.state(seen)
                except (RuntimeError, ValueError):
                    AppHelper.callAfter(NSApp.terminate_, None)
                    return
                if new["revision"] != seen:
                    seen = new["revision"]
                    AppHelper.callAfter(self.set_state, new)

        @objc.python_method
        def set_state(self, new):
            if self.canvas is not None:
                self.canvas.set_state(new)

        @objc.python_method
        def draw_test(self):
            """CI only: the full view draws whole, then (a hover) only what changed."""
            canvas = self.canvas
            canvas.invalidate()
            canvas.display()
            whole = canvas.picture is not None
            canvas.view.mouse_move(120, 140)
            canvas.invalidate()
            canvas.display()
            log.warning("panel test: full view drawn=%s, then %s changed area(s) redrawn (process %s)", whole,
                        "all" if canvas.view.changed is None else len(canvas.view.changed), os.getpid())

    app = NSApplication.sharedApplication()
    delegate = FullViewApp.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop(installInterrupt=True)


class Started:
    """The full view's process, as the menu bar app sees it."""

    def __init__(self, popen=None):
        self.popen, self.app = popen, None  # app: its NSRunningApplication, once Launch Services has it

    def alive(self):
        if self.popen is not None:
            return self.popen.poll() is None
        return self.app is None or not self.app.isTerminated()  # None: still starting

    def close(self):
        if self.popen is not None:
            self.popen.terminate()
        elif self.app is not None:
            self.app.terminate()

    def front(self):
        if self.app is not None:
            self.app.activateWithOptions_(1 << 1)  # NSApplicationActivateIgnoringOtherApps


def start(url):
    """From the menu bar app: start the full view's process."""
    env = dict(os.environ, **{ENV: url})
    from AppKit import NSBundle
    bundle = NSBundle.mainBundle()
    if not str(bundle.bundlePath()).endswith(".app"):  # run from a terminal (development)
        import subprocess
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "LimitSwitcher.pyw")
        return Started(subprocess.Popen([sys.executable, script, "--full-view"], env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True))
    # Through Launch Services, as a second instance of LimitSwitcher.app: its own name and icon in
    # the Dock and the menu bar, and macOS lets it come to the front.
    from AppKit import NSWorkspace, NSWorkspaceOpenConfiguration
    config = NSWorkspaceOpenConfiguration.configuration()
    config.setCreatesNewApplicationInstance_(True)
    config.setActivates_(True)
    config.setAddsToRecentItems_(False)
    config.setEnvironment_(env)
    config.setArguments_(["--full-view"])  # only shows in ps / Activity Monitor; the environment decides
    started = Started()

    def done(app, error):
        if error is not None:
            log.warning("the full view couldn't start: %s", error)
        started.app = app
    NSWorkspace.sharedWorkspace().openApplicationAtURL_configuration_completionHandler_(
        bundle.bundleURL(), config, done)
    return started
