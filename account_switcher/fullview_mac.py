"""macOS host for the full view: an NSView that shows frames drawn by fullview.FullView.

No WebKit: the window's content is this one view, drawn at the display's backing scale. It runs
on the main thread with the rest of the menu bar app; timers are one-shot callLater()s, only
while something is due. The view (and its cached tiles) goes away when the window closes.
"""
import io
import logging

import objc
from AppKit import (NSBitmapImageRep, NSCompositingOperationCopy, NSCursor, NSEventModifierFlagCommand, NSImage,
                    NSPasteboard, NSTrackingActiveInKeyWindow, NSTrackingArea, NSTrackingInVisibleRect,
                    NSTrackingMouseEnteredAndExited, NSTrackingMouseMoved, NSView)
from Foundation import NSData, NSMakeSize, NSZeroRect
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
        self.picture = None      # NSImage of the last frame
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
        if self.dirty or self.picture is None:
            image = self.view.frame()
            data = io.BytesIO()
            image.save(data, "TIFF")  # uncompressed: a straight copy AppKit reads as is
            raw = data.getvalue()
            rep = NSBitmapImageRep.imageRepWithData_(NSData.dataWithBytes_length_(raw, len(raw)))
            size = NSMakeSize(self.view.width, self.view.height)
            rep.setSize_(size)  # logical size: drawn at the backing scale
            picture = NSImage.alloc().initWithSize_(size)
            picture.addRepresentation_(rep)
            self.picture, self.dirty = picture, False
        self.picture.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            self.bounds(), NSZeroRect, NSCompositingOperationCopy, 1.0, True, None)

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
        self.view = self.picture = None
