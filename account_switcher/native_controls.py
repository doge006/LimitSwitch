"""Small native controls with finite, interruptible motion and no idle timers."""
from __future__ import annotations

import ctypes
import time
import tkinter as tk
from tkinter import font as tkfont
from collections.abc import Callable

from PIL import Image, ImageDraw, ImageTk


def animations_enabled() -> bool:
    """Read Windows' Accessibility > Visual effects > Animation effects setting."""
    try:
        enabled = ctypes.c_int()
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    except (AttributeError, OSError):
        pass
    return False


class Motion:
    """Animate one scalar from its current value; replacing a target cancels its end callback."""

    def __init__(self, widget, initial: float, render: Callable[[float], None]):
        self.widget, self.value, self.render = widget, float(initial), render
        self.job = None
        self._generation = 0
        widget.bind("<Destroy>", self._destroyed, add="+")

    def _destroyed(self, event):
        if event.widget is self.widget:
            self.cancel()

    def cancel(self):
        self._generation += 1
        if self.job is not None:
            try:
                self.widget.after_cancel(self.job)
            except tk.TclError:
                pass
            self.job = None

    def to(self, target: float, duration=180, done=None, animate=True):
        self.cancel()
        generation = self._generation
        target = float(target)
        if not animate or not animations_enabled() or duration <= 0 or target == self.value:
            self.value = target
            self.render(target)
            if done and generation == self._generation:
                done()
            return
        start, started = self.value, time.monotonic()

        def frame():
            self.job = None
            if generation != self._generation:
                return
            progress = min(1., max(0., (time.monotonic() - started) * 1000 / duration))
            self.value = start + (target - start) * (1 - (1 - progress) ** 3)
            self.render(self.value)
            if generation != self._generation:
                return
            if progress < 1:
                self.job = self.widget.after(16, frame)
            elif done:
                done()

        frame()


def _mix(start: str, end: str, fraction: float) -> str:
    rgb = [round(int(start[i:i+2], 16) * (1-fraction) + int(end[i:i+2], 16) * fraction)
           for i in (1, 3, 5)]
    return "#" + "".join(f"{channel:02x}" for channel in rgb)


class FluentButton(tk.Canvas):
    """Rounded, keyboard-accessible button; command fires on release inside its bounds."""

    def __init__(self, parent, *, text="", command=None, width=None, **kwargs):
        try:
            background = parent.cget("background")
        except tk.TclError:
            background = "#1e1e1e"
        self._text, self._command = text, command
        self._disabled = kwargs.pop("state", "normal") == "disabled"
        self._inside = self._held = self._focused = False
        self._hover = self._press = 0.
        self._width_chars = width
        self._font = tkfont.Font(root=parent, family="Segoe UI", size=9)
        self._photo = None
        self._alive = True
        super().__init__(parent, width=self._requested_width(), height=36,
                         bg=background, highlightthickness=0, bd=0,
                         takefocus=0 if self._disabled else 1,
                         cursor="arrow" if self._disabled else "hand2", **kwargs)
        self._surface = self.create_image(0, 0, anchor="nw")
        self._label = self.create_text(0, 0, text=text, fill="#eeeeee", font=self._font)
        self.hover_motion = Motion(self, 0., self._set_hover)
        self.press_motion = Motion(self, 0., self._set_press)
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._down)
        self.bind("<ButtonRelease-1>", self._up)
        self.bind("<FocusIn>", lambda event: self._focus(True))
        self.bind("<FocusOut>", lambda event: self._focus(False))
        self.bind("<Return>", self._keyboard)
        self.bind("<space>", self._keyboard)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._draw()

    def _requested_width(self):
        content = self._font.measure("0" * self._width_chars) if self._width_chars is not None else self._font.measure(self._text)
        return max(64, content + 28)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, dict):
            kwargs = {**cnf, **kwargs}
            cnf = None
        if cnf is not None or not kwargs:
            return super().configure(cnf, **kwargs)
        resize = False
        if "text" in kwargs:
            self._text = str(kwargs.pop("text"))
            resize = self._width_chars is None
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "width" in kwargs:
            self._width_chars = kwargs.pop("width")
            resize = True
        if "state" in kwargs:
            state = kwargs.pop("state")
            if state not in ("normal", "disabled"):
                raise ValueError("FluentButton state must be 'normal' or 'disabled'")
            self._disabled = state == "disabled"
            kwargs.update(takefocus=0 if self._disabled else 1,
                          cursor="arrow" if self._disabled else "hand2")
            if self._disabled:
                self._held = False
                self.hover_motion.to(0, animate=False)
                self.press_motion.to(0, animate=False)
        if resize:
            kwargs["width"] = self._requested_width()
        if kwargs:
            super().configure(**kwargs)
        self._draw()

    config = configure

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _set_press(self, value):
        self._press = value
        self._draw()

    def _focus(self, focused):
        self._focused = focused
        self._draw()

    def _enter(self, event):
        self._inside = True
        if not self._disabled:
            self.hover_motion.to(1, duration=120)
            if self._held:
                self.press_motion.to(1, duration=100)

    def _leave(self, event):
        self._inside = False
        self.hover_motion.to(0, duration=120)
        self.press_motion.to(0, duration=120)

    def _down(self, event):
        if not self._disabled:
            self._held = True
            self.focus_set()
            self.press_motion.to(1, duration=100)
        return "break"

    def _up(self, event):
        activate = self._held and not self._disabled and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._held = False
        self.press_motion.to(0, duration=150)
        if activate:
            self.invoke()
        return "break"

    def _keyboard(self, event):
        if not self._disabled:
            self._held = False
            self.press_motion.to(0, animate=False)
            self.hover_motion.to(0, animate=False)
            self.invoke()
        return "break"

    def invoke(self):
        if not self._disabled and self._command:
            return self._command()

    def _destroyed(self, event):
        if event.widget is self:
            self._alive = False
            self.hover_motion.cancel()
            self.press_motion.cancel()

    def _draw(self, event=None):
        if not self._alive:
            return
        width, height = max(self.winfo_width(), self._requested_width()), max(self.winfo_height(), 36)
        scale = 3
        background = tuple(channel // 257 for channel in self.winfo_rgb(self.cget("background")))
        image = Image.new("RGB", (width * scale, height * scale), background)
        draw = ImageDraw.Draw(image)
        inset = 2 + self._press
        bounds = tuple(round(value * scale) for value in (inset, inset, width-inset-1, height-inset-1))
        fill = "#272727" if self._disabled else _mix(_mix("#303030", "#3a3a3a", self._hover), "#292929", self._press)
        border = "#363636" if self._disabled else _mix("#454545", "#575757", self._hover)
        draw.rounded_rectangle(bounds, radius=7 * scale, fill=fill, outline=border, width=scale)
        if self._focused and not self._disabled:
            draw.rounded_rectangle((0, 0, (width-1)*scale, (height-1)*scale), radius=9*scale, outline="#9ccaf2", width=scale)
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.itemconfigure(self._surface, image=self._photo)
        self.itemconfigure(self._label, text=self._text, fill="#858585" if self._disabled else "#eeeeee")
        self.coords(self._label, width / 2, height / 2 + self._press * .6)
