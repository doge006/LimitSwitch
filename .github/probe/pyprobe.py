"""CI control: which part of the menu bar setup macOS 26 rejects. Prints the status item's
frame for one variant: title | symbol | symbol+regular | symbol+regular-later | symbol+menu."""
import sys

from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular,
                    NSImage, NSMenu, NSStatusBar, NSVariableStatusItemLength)
from AppKit import NSPopover, NSViewController, NSWindow
from Foundation import NSMakeRect, NSObject, NSURL, NSURLRequest
from PyObjCTools import AppHelper

VARIANT = sys.argv[1]


class Delegate(NSObject):
    def applicationDidFinishLaunching_(self, _note):
        if "menu" in VARIANT:
            NSApp.setMainMenu_(NSMenu.alloc().init())
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        if VARIANT == "title":
            self.item.button().setTitle_("PY")
        else:
            image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("arrow.triangle.2.circlepath", "x")
            image.setTemplate_(True)
            self.item.button().setImage_(image)
        if VARIANT.endswith("+regular"):
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        if VARIANT.endswith("+regular-later"):
            AppHelper.callLater(1, NSApp.setActivationPolicy_, NSApplicationActivationPolicyRegular)
        if "popover" in VARIANT:
            from WebKit import WKWebView, WKWebViewConfiguration
            view = WKWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, 340, 420),
                                                                  WKWebViewConfiguration.alloc().init())
            view.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_("about:blank")))
            controller = NSViewController.alloc().init()
            controller.setView_(view)
            self.popover = NSPopover.alloc().init()
            self.popover.setContentViewController_(controller)
        if "window" in VARIANT:
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 800, 600), 15 | (1 << 15), 2, False)
            self.window.center()
            NSApp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
            NSApp.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)
        if "tooltip" in VARIANT:
            self.item.button().setToolTip_("tip")
        if "action" in VARIANT:
            self.item.button().setTarget_(self)
            self.item.button().setAction_("report:")
            self.item.button().sendActionOn_((1 << 2) | (1 << 4))
        AppHelper.callLater(3, self.report)

    def report(self):
        f = self.item.button().window().frame()
        print(VARIANT, "frame:", f.origin.x, f.origin.y, f.size.width, f.size.height, flush=True)
        NSApp.terminate_(None)


app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
delegate = Delegate.alloc().init()
app.setDelegate_(delegate)
AppHelper.runEventLoop()
