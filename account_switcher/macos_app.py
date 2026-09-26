"""macOS menu bar app: the Mac counterpart of the Windows tray (tray.py + flyout.py).

- Menu bar icon (an SF Symbol, drawn as a template image so it matches the menu bar).
- Click: a native popover with the compact panel (static/menu.html in a transparent
  WKWebView over the popover's own material). Drag it away from the menu bar and it detaches
  into its own floating window: the Mac version of "pop out".
- Right-click (or Control-click): a native menu.
- Full View: a native window with the dashboard.
- Notifications when Auto swap moves an account.
No Dock icon (accessory app). Built on PyObjC (pyobjc-framework-Cocoa and -WebKit).
"""
import os
import subprocess
import threading
import time

import logging
from pathlib import Path

import objc
from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular,
                    NSBackingStoreBuffered, NSColor, NSEventModifierFlagCommand, NSEventModifierFlagOption,
                    NSEventMaskLeftMouseUp, NSEventMaskRightMouseUp, NSEventModifierFlagControl,
                    NSEventTypeRightMouseUp, NSImage, NSMenu, NSMenuItem, NSMinYEdge, NSOffState, NSOnState,
                    NSPopover, NSPopoverBehaviorTransient, NSStatusBar, NSVariableStatusItemLength, NSViewController,
                    NSWindow, NSWindowStyleMaskClosable, NSWindowStyleMaskFullSizeContentView,
                    NSWindowStyleMaskMiniaturizable, NSWindowStyleMaskResizable, NSWindowStyleMaskTitled)
from Foundation import NSMakeRect, NSMakeSize, NSObject, NSURL, NSURLRequest
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration

from .tray import APP, PROVIDERS, active_accounts, short_name, tooltip, tray_level

log = logging.getLogger("account_switcher.macos")
PANEL_WIDTH = 392
ICON = Path(__file__).resolve().parent / "static" / "assets" / "appicon-mac.png"  # the Dock icon, macOS shape
TERMINATE_NOW = 1  # NSTerminateNow
SYMBOLS = {None: "arrow.triangle.2.circlepath", "good": "arrow.triangle.2.circlepath",
           "warn": "arrow.triangle.2.circlepath", "bad": "exclamationmark.arrow.triangle.2.circlepath"}


def notify(title, text):
    """A standard macOS notification (Notification Center)."""
    script = f'display notification {_as(text)} with title {_as(title)}'
    subprocess.Popen(["/usr/bin/osascript", "-e", script], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _as(text):
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def protocols(*names):
    """The named Objective-C protocols that exist here. Conforming is only a declaration: a
    missing one (older macOS, or not loaded) must not stop the app from starting."""
    found = []
    for name in names:
        try:
            found.append(objc.protocolNamed(name))
        except Exception:
            pass
    return found


def web_view(url, frame, transparent=False, handler=None):
    config = WKWebViewConfiguration.alloc().init()
    if handler is not None:
        config.userContentController().addScriptMessageHandler_name_(handler, "app")
    view = WKWebView.alloc().initWithFrame_configuration_(frame, config)
    if transparent:
        view.setValue_forKey_(False, "drawsBackground")  # let the popover material show through
        if view.respondsToSelector_("setUnderPageBackgroundColor:"):
            view.setUnderPageBackgroundColor_(NSColor.clearColor())
    view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))
    return view


class Bridge(NSObject, protocols=protocols("WKScriptMessageHandler")):
    """Messages from the panel page: its height, and Full View / Quit."""

    def initWithApp_(self, app):
        self = objc.super(Bridge, self).init()
        self.app = app
        return self

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        body = message.body()
        kind = body.get("type") if hasattr(body, "get") else None
        if kind == "height":
            self.app.resize_panel(float(body.get("value") or 0))
        elif kind == "full":
            self.app.popover.performClose_(None)
            self.app.showFullView_(None)
        elif kind == "quit":
            self.app.quit_(None)


class MenuBarApp(NSObject, protocols=protocols("NSPopoverDelegate")):
    def initWithController_server_openNow_cleanup_(self, controller, server, open_now, cleanup):
        self = objc.super(MenuBarApp, self).init()
        self.controller, self.server, self.open_now, self.cleanup = controller, server, open_now, cleanup
        self.url = server.launch_url
        self.state = controller.snapshot()
        self.last_active = {a["provider"]: a["id"] for a in active_accounts(self.state)}
        self.full_window = None
        self.quitting = False
        return self

    # ---------- setup ----------
    def applicationDidFinishLaunching_(self, _note):
        NSApp.setMainMenu_(self.main_menu())
        icon = NSImage.alloc().initWithContentsOfFile_(str(ICON))
        self.icon = icon
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.setAutosaveName_("AccountSwitcher")  # macOS remembers its place and visibility by this
        self.item.setVisible_(True)
        button = self.item.button()
        button.setTarget_(self)
        button.setAction_("statusClicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.shown_level = object()
        self.bridge = Bridge.alloc().initWithApp_(self)
        base, token = self.url.split("/#token=")
        view = web_view(f"{base}/menu#token={token}", NSMakeRect(0, 0, PANEL_WIDTH, 420), transparent=True,
                        handler=self.bridge)
        controller = NSViewController.alloc().init()
        controller.setView_(view)
        self.popover = NSPopover.alloc().init()
        self.popover.setContentViewController_(controller)
        self.popover.setContentSize_(NSMakeSize(PANEL_WIDTH, 420))
        self.popover.setBehavior_(NSPopoverBehaviorTransient)
        from AppKit import NSAppearance
        dark = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
        if dark is not None:
            self.popover.setAppearance_(dark)  # the dark panel design, like the Windows tray
        self.popover.setAnimates_(True)
        self.popover.setDelegate_(self)
        self.server.quit = lambda: AppHelper.callAfter(self.quit_, None)  # the dashboard's Quit
        self.server.show = lambda: AppHelper.callAfter(self.showFullView_, None)  # opened again (Spotlight, Finder)
        self.refresh()
        threading.Thread(target=self.watch, daemon=True).start()
        if self.open_now:
            self.showFullView_(None)
        AppHelper.callLater(4, self.check_status_item)

    @objc.python_method
    def check_status_item(self):
        """macOS gives a status item it won't show a height of 0 (it's hidden: not allowed in
        the menu bar, or no room). Then the app would be invisible, so say so and open the window."""
        if os.environ.get("ACCOUNT_SWITCHER_DEBUG"):
            self.describe_status_item()
        window = self.item.button().window() if self.item.button() is not None else None
        if window is None or window.frame().size.height > 0:
            return
        self.describe_status_item()
        self.showFullView_(None)
        from AppKit import NSAlert, NSUserDefaults
        defaults = NSUserDefaults.standardUserDefaults()
        if defaults.boolForKey_("HiddenIconAlertSuppressed"):
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_("macOS is hiding Account Switcher's menu bar icon")
        alert.setInformativeText_("Turn on Account Switcher under System Settings › Menu Bar › Allow in the Menu Bar. "
                                  "If it's already on, the menu bar may be full: quit another menu bar app, or hold ⌘ "
                                  "and drag icons out to make room. Everything also works from this window.")
        alert.addButtonWithTitle_("Open Menu Bar Settings")
        alert.addButtonWithTitle_("OK")
        alert.setShowsSuppressionButton_(True)
        NSApp.activateIgnoringOtherApps_(True)
        choice = alert.runModal()
        if alert.suppressionButton().state():
            defaults.setBool_forKey_(True, "HiddenIconAlertSuppressed")
        if choice == 1000:  # NSAlertFirstButtonReturn
            subprocess.Popen(["/usr/bin/open", "x-apple.systempreferences:com.apple.ControlCenter-Settings.extension"])

    @objc.python_method
    def describe_status_item(self):
        button = self.item.button()
        window = button.window() if button is not None else None
        frame = window.frame() if window is not None else None
        log.warning("status item: visible=%s button=%s window=%s frame=%s onscreen=%s occlusion=%s level=%s number=%s",
                    self.item.isVisible(), button is not None, window is not None,
                    None if frame is None else (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height),
                    None if window is None else window.isVisible(),
                    None if window is None else window.occlusionState(),
                    None if window is None else window.level(),
                    None if window is None else window.windowNumber())

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
                      None, (f"Quit {APP}", "quit:", "q", "self")])
        submenu("Edit", [("Undo", "undo:", "z"), ("Redo", "redo:", "Z"), None, ("Cut", "cut:", "x"),
                         ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")])
        window = submenu("Window", [("Minimize", "performMiniaturize:", "m"), ("Close", "performClose:", "w"), None,
                                    ("Show Account Switcher", "showFullView:", "0", "self")])
        NSApp.setWindowsMenu_(window)
        return bar

    def applicationShouldTerminate_(self, _app):
        """Every way of quitting (⌘Q, the Dock, the menus, logging out) ends here. Cocoa ends the
        process right after, so undo the Codex / Claude changes now."""
        try:
            self.cleanup()
        except Exception:
            log.exception("cleanup on quit failed")
        return TERMINATE_NOW

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _visible):
        self.showFullView_(None)  # clicked in the Dock or opened again
        return False

    def windowWillClose_(self, _note):
        # Back to a menu bar app: no Dock icon once the window is closed.
        AppHelper.callAfter(NSApp.setActivationPolicy_, NSApplicationActivationPolicyAccessory)

    def popoverShouldDetach_(self, _popover):
        return True  # drag it off the menu bar to keep it open as a floating panel

    # ---------- status item ----------
    def statusClicked_(self, sender):
        event = NSApp.currentEvent()
        if event is not None and (event.type() == NSEventTypeRightMouseUp
                                  or event.modifierFlags() & NSEventModifierFlagControl):
            self.item.setMenu_(self.build_menu())
            self.item.button().performClick_(None)  # shows the menu
            self.item.setMenu_(None)  # so the next left click opens the panel again
            return
        self.togglePanel_(sender)

    def togglePanel_(self, _sender):
        if self.popover.isShown():
            self.popover.performClose_(None)
            return
        button = self.item.button()
        NSApp.activateIgnoringOtherApps_(True)
        self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSMinYEdge)
        try:
            self.controller.action("refresh", {"ifOlderThan": 45})  # like opening the Windows panel
        except (RuntimeError, ValueError):
            pass

    @objc.python_method
    def resize_panel(self, height):
        if height > 0:
            self.popover.setContentSize_(NSMakeSize(PANEL_WIDTH, min(height, 760)))

    @objc.python_method
    def build_menu(self):
        state = self.controller.snapshot()
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        def add(title, action, key="", checked=None, enabled=True):
            item = menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
            item.setTarget_(self)
            item.setEnabled_(enabled)
            if checked is not None:
                item.setState_(NSOnState if checked else NSOffState)
            return item

        add("Open Panel", "togglePanel:")
        add("Full View…", "showFullView:")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Auto Swap", "toggleAutoSwap:", checked=state["autoSwap"], enabled=not state["busy"])
        add("AFK Mode", "toggleAfk:", checked=state["afk"], enabled=not state["busy"])
        menu.addItem_(NSMenuItem.separatorItem())
        add(f"Quit {APP}", "quit:", "q")
        return menu

    def toggleAutoSwap_(self, _sender):
        self.set_pref("autoSwap")

    def toggleAfk_(self, _sender):
        self.set_pref("afk")

    @objc.python_method
    def set_pref(self, key):
        state = self.controller.snapshot()
        prefs = {"autoSwap": state["autoSwap"], "afk": state["afk"]}
        prefs[key] = not prefs[key]
        try:
            self.controller.action("preferences", prefs)
        except (RuntimeError, ValueError) as error:
            notify(APP, str(error))

    # ---------- full view ----------
    def showFullView_(self, _sender):
        if self.full_window is None:
            style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
                     | NSWindowStyleMaskResizable | NSWindowStyleMaskFullSizeContentView)
            window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 1180, 860), style, NSBackingStoreBuffered, False)
            window.setTitle_(APP)
            window.setTitlebarAppearsTransparent_(True)
            window.setTitleVisibility_(1)  # NSWindowTitleHidden: the page has its own header
            window.setReleasedWhenClosed_(False)
            window.setMinSize_(NSMakeSize(720, 520))
            window.setContentView_(web_view(self.url, NSMakeRect(0, 0, 1180, 860)))
            window.center()
            window.setFrameAutosaveName_("AccountSwitcherFullView")
            window.setDelegate_(self)
            self.full_window = window
        # A window gets a Dock icon and a menu bar like any app, so it can be found and quit.
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        if self.icon is not None:
            NSApp.setApplicationIconImage_(self.icon)  # the Dock would show Python's icon otherwise
        NSApp.activateIgnoringOtherApps_(True)
        self.full_window.makeKeyAndOrderFront_(None)

    # ---------- state ----------
    @objc.python_method
    def refresh(self):
        state = self.state = self.controller.snapshot()
        level = tray_level(state)
        if level != self.shown_level:
            self.shown_level = level
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(SYMBOLS.get(level), APP)
            if image is None:  # older macOS: plain text
                self.item.button().setTitle_("⇄")
            else:
                image.setTemplate_(True)
                self.item.button().setImage_(image)
        self.item.button().setToolTip_(tooltip(state))
        self.announce_failovers(state)

    @objc.python_method
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
            notify(f"{dict(PROVIDERS)[provider]} switched accounts", f"{reason}Now using {short_name(account)}.")

    @objc.python_method
    def watch(self):
        """Block until the controller changes, then refresh on the main thread."""
        seen = self.state["revision"]
        condition = self.controller.condition
        while True:
            with condition:
                condition.wait_for(lambda: self.controller.revision != seen or self.controller.closed)
                if self.controller.closed:
                    break
            time.sleep(.15)
            seen = self.controller.revision
            AppHelper.callAfter(self.refresh)
        AppHelper.callAfter(self.quit_, None)

    def quit_(self, _sender):
        if not self.quitting:
            self.quitting = True
            self.popover.performClose_(None)
            NSApp.terminate_(None)  # -> applicationShouldTerminate_, which cleans up


def run(controller, server, open_now=False, cleanup=lambda: None):
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # menu bar only, no Dock icon
    delegate = MenuBarApp.alloc().initWithController_server_openNow_cleanup_(controller, server, open_now, cleanup)
    app.setDelegate_(delegate)
    AppHelper.runEventLoop(installInterrupt=True)
