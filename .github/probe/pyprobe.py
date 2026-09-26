"""CI control: which part of the menu bar setup macOS 26 rejects. Prints the status item's
frame for one variant: title | symbol | symbol+regular | symbol+regular-later | symbol+menu."""
import sys

from AppKit import (NSApp, NSApplication, NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular,
                    NSImage, NSMenu, NSStatusBar, NSVariableStatusItemLength)
from Foundation import NSObject
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
