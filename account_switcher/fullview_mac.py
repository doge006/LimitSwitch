"""macOS host for the full view: an NSView that shows what fullview.FullView draws.

No WebKit, and no picture of the whole window: FullView.scene() gives the cards as cached images
and small patches for what changes (bars, percentages, menus, toasts), and this view puts them on
screen with Core Graphics over the background colour. At Retina scale a window-sized image is
several MB; the only one left is the window's own. Timers are one-shot callLater()s, only while
something is due. It runs in the full view's own process (fullview_mac_app.py).
"""
import logging

import objc
from AppKit import (NSCursor, NSEventModifierFlagCommand, NSGraphicsContext, NSPasteboard, NSTrackingActiveInKeyWindow, NSTrackingArea, NSTrackingInVisibleRect,
                    NSTrackingMouseEnteredAndExited, NSTrackingMouseMoved, NSView)
from Foundation import NSData
from PyObjCTools import AppHelper

from . import fullview_render as vr
from .fullview import FullView

log = logging.getLogger("account_switcher.fullview")
KEYS = {53: "escape", 36: "enter", 76: "enter", 51: "backspace", 117: "delete", 123: "left", 124: "right",
        115: "home", 119: "end"}


class FullViewCanvas(NSView):
    def initWithFrame_controller_state_(self, frame, controller, state):
        self = objc.super(FullViewCanvas, self).initWithFrame_(frame)
        if self is None:
            return None
        self.view = FullView(controller, self, state)
        self.scene = None        # FullView.scene(): what is on screen
        self.cg = {}             # id(tile) -> (tile, CGImage): the cards, made once each
        self.fill = None         # 1x1 CGImage of the background colour
        self.dirty = True
        self.cursor_kind = "arrow"
        self.timers = {}         # name -> generation (a later set/kill makes an earlier callLater a no-op)
        self.addTrackingArea_(NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            frame, NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited | NSTrackingActiveInKeyWindow
            | NSTrackingInVisibleRect, self, None))
        return self

    # ---------- NSView ----------
    def isFlipped(self):
        return True  # top-left origin, like the drawing

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, _event):
        return True

    def isOpaque(self):
        return True

    @objc.python_method
    def scale(self):
        window = self.window()
        return float(window.backingScaleFactor()) if window is not None else 2.0

    @objc.python_method
    def sync_size(self):
        size = self.bounds().size
        if size.width > 1 and size.height > 1 and self.view is not None:
            self.view.resize(float(size.width), float(size.height), self.scale())
            self.invalidate()

    def setFrameSize_(self, size):
        objc.super(FullViewCanvas, self).setFrameSize_(size)
        self.sync_size()

    def viewDidMoveToWindow(self):
        self.sync_size()
        if self.window() is not None:
            self.window().makeFirstResponder_(self)

    def viewDidChangeBackingProperties(self):
        self.sync_size()  # moved to a display with another scale: redraw sharp

    def drawRect_(self, _rect):
        if self.view is None or not self.view.width:
            return
        import Quartz
        if self.dirty or self.scene is None:
            self.scene = self.view.scene()
            self.dirty = False
        context = NSGraphicsContext.currentContext().CGContext()
        bounds = self.bounds()
        height = float(bounds.size.height)
        s = self.view.scale
        Quartz.CGContextSaveGState(context)
        Quartz.CGContextTranslateCTM(context, 0, height)  # this view is flipped; CG draws bottom-up
        Quartz.CGContextScaleCTM(context, 1, -1)
        Quartz.CGContextSetInterpolationQuality(context, Quartz.kCGInterpolationNone)
        if self.fill is None:  # the background through the same colour path as the images on it
            self.fill = image_from(Quartz, bytes(vr.BG) + b"\xff", 1, 1, Quartz.kCGImageAlphaNoneSkipLast)
        Quartz.CGContextDrawImage(context, Quartz.CGRectMake(0, 0, bounds.size.width, height), self.fill)
        cached = {}
        for tile, x, y, alpha in self.scene["tiles"]:
            held = self.cg.get(id(tile))
            if held is None or held[0] is not tile:
                premultiplied = tile.image.convert("RGBa")  # the form Core Graphics composites fastest
                held = (tile, image_from(Quartz, premultiplied.tobytes(), tile.image.width, tile.image.height,
                                         Quartz.kCGImageAlphaPremultipliedLast))
                del premultiplied
            cached[id(tile)] = held
            Quartz.CGContextSetAlpha(context, max(0.0, min(1.0, alpha)))
            self.put(Quartz, context, held[1], x, y, tile.image.width, tile.image.height, s, height)
        Quartz.CGContextSetAlpha(context, 1.0)
        self.cg = cached  # cards no longer shown are let go
        for patch, x, y in self.scene["patches"]:
            picture = image_from(Quartz, patch.tobytes("raw", "RGBX"), patch.width, patch.height,
                                 Quartz.kCGImageAlphaNoneSkipLast)
            self.put(Quartz, context, picture, x, y, patch.width, patch.height, s, height)
        Quartz.CGContextRestoreGState(context)
        if not self.has_timer("anim"):  # still: what drawing freed goes back to macOS
            from .memory import trim_soon
            trim_soon(3.0)

    @objc.python_method
    def put(self, Quartz, context, picture, x, y, w, h, scale, height):
        """Draw a device-px image whose top left is at device (x, y)."""
        Quartz.CGContextDrawImage(context, Quartz.CGRectMake(x / scale, height - (y + h) / scale, w / scale, h / scale),
                                  picture)

    # ---------- input ----------
    @objc.python_method
    def point(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        return float(p.x), float(p.y)

    def mouseMoved_(self, event):
        if self.view is not None:
            self.view.mouse_move(*self.point(event))

    def mouseDragged_(self, event):
        self.mouseMoved_(event)

    def mouseExited_(self, _event):
        if self.view is not None:
            self.view.mouse_leave()
            NSCursor.arrowCursor().set()

    def mouseDown_(self, event):
        if self.view is not None:
            self.view.mouse_down(*self.point(event))

    def mouseUp_(self, event):
        if self.view is not None:
            self.view.mouse_up(*self.point(event))

    def scrollWheel_(self, event):
        if self.view is None:
            return
        dy = float(event.scrollingDeltaY())
        if not event.hasPreciseScrollingDeltas():
            dy *= vr.SCROLL_STEP / 3  # a mouse wheel: lines
        self.view.wheel(-dy)

    def keyDown_(self, event):
        if self.view is None:
            return
        command = bool(event.modifierFlags() & NSEventModifierFlagCommand)
        chars = event.charactersIgnoringModifiers() or ""
        name = KEYS.get(event.keyCode())
        if command and chars.lower() in ("a", "v"):
            self.view.key(chars.lower(), True)
        elif name:
            self.view.key(name)
        elif not command and event.characters():
            self.view.char(event.characters())
        elif command:
            objc.super(FullViewCanvas, self).keyDown_(event)  # ⌘W, ⌘Q and the like go to the menus

    # ---------- the host interface FullView uses ----------
    @objc.python_method
    def invalidate(self):
        self.dirty = True
        self.setNeedsDisplay_(True)

    @objc.python_method
    def set_timer(self, name, ms):
        generation = self.timers.get(name, 0) + 1
        self.timers[name] = generation
        AppHelper.callLater(max(10, ms) / 1000, self.fire, name, generation)

    @objc.python_method
    def fire(self, name, generation):
        if self.timers.get(name) == generation and self.view is not None:
            del self.timers[name]
            self.view.timer(name)

    @objc.python_method
    def kill_timer(self, name):
        if name in self.timers:
            self.timers[name] = self.timers[name] + 1000  # outstanding callLater()s find a newer generation
            del self.timers[name]

    @objc.python_method
    def has_timer(self, name):
        return name in self.timers

    @objc.python_method
    def set_cursor(self, kind):
        if kind != self.cursor_kind:
            self.cursor_kind = kind
        {"hand": NSCursor.pointingHandCursor, "text": NSCursor.IBeamCursor}.get(kind, NSCursor.arrowCursor)().set()

    @objc.python_method
    def clipboard(self):
        return NSPasteboard.generalPasteboard().stringForType_("public.utf8-plain-text") or ""

    @objc.python_method
    def set_state(self, state):
        if self.view is not None:
            self.view.set_state(state)

    @objc.python_method
    def close(self):
        """The window closed: stop timers, drop the frames and tiles."""
        self.timers = {}
        if self.view is not None:
            self.view.close()
        self.view = self.scene = None
        self.cg = {}


def image_from(Quartz, raw, width, height, alpha_info):
    """A CGImage over `raw` (8-bit RGBA-ordered pixels, width * 4 bytes a row)."""
    data = NSData.dataWithBytes_length_(raw, len(raw))
    return Quartz.CGImageCreate(width, height, 8, 32, width * 4, Quartz.CGColorSpaceCreateDeviceRGB(), alpha_info,
                                Quartz.CGDataProviderCreateWithCFData(data), None, False,
                                Quartz.kCGRenderingIntentDefault)
