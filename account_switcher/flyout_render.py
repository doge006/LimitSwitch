"""Draws the tray flyout as an image plus clickable regions. Pure Pillow; no windowing.

Shapes are drawn at 3x and downsampled so edges are anti-aliased; text is drawn at
final size by FreeType (already smooth). Everything is in logical pixels times `scale`.
"""
from functools import lru_cache
import os
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).with_name("static") / "assets"
PROVIDERS = (("claude", "Claude"), ("codex", "Codex"))
SS = 2  # supersampling factor for shapes

WIDTH = 372         # panel width
MARGIN = 18         # transparent margin that holds the shadow
RADIUS = 8
ROW_H = 54
BG = (32, 32, 32, 255)
FOOTER = (27, 27, 27, 255)
BORDER = (255, 255, 255, 22)
TEXT = (243, 243, 243, 255)
MUTED = (163, 163, 163, 255)
FAINT = (140, 140, 140, 255)
TRACK = (255, 255, 255, 26)
HOVER = (255, 255, 255, 13)
ACTIVE = (255, 255, 255, 8)
PILL = (255, 255, 255, 16)
PILL_HOVER = (255, 255, 255, 28)
GOOD, WARN, BAD = (76, 195, 138, 255), (229, 181, 74, 255), (239, 106, 91, 255)
ACCENT = {"claude": (224, 138, 104, 255), "codex": (162, 149, 247, 255)}


# ---------- data helpers ----------
def remaining(used):
    return max(0, min(100, 100 - used))


def level_rgb(left):
    return GOOD if left > 30 else WARN if left > 10 else BAD


def short_name(account):
    return account["alias"].split(" · ")[-1].replace(" (synthetic)", "")


def short_label(window):
    if window["key"] == "five_hour":
        return "5h"
    if window["key"] == "weekly":
        return "1w"
    return window["label"].split(" · ")[-1]


def until(ts):
    m = max(0, int((ts - time.time()) / 60))
    return f"{m // 1440}d {m % 1440 // 60}h" if m >= 1440 else f"{m // 60}h {m % 60}m"


# ---------- fonts & images ----------
def _font_files():
    if sys.platform == "win32":
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        return fonts / "segoeui.ttf", fonts / "seguisb.ttf"
    dejavu = Path("/usr/share/fonts/truetype/dejavu")
    return dejavu / "DejaVuSans.ttf", dejavu / "DejaVuSans-Bold.ttf"


@lru_cache(maxsize=32)
def font(size, bold, scale):
    regular, semibold = _font_files()
    try:
        return ImageFont.truetype(str(semibold if bold else regular), round(size * scale))
    except OSError:
        return ImageFont.load_default(round(size * scale))


@lru_cache(maxsize=16)
def asset(name, px):
    with Image.open(ASSETS / f"{name}.png") as source:
        return source.convert("RGBA").resize((px, px), Image.Resampling.LANCZOS)


# ---------- layout ----------
class Layout:
    """Collects shapes, text, images and hit regions in logical pixels."""

    def __init__(self):
        self.shapes, self.texts, self.images, self.hits = [], [], [], []

    def rect(self, x, y, w, h, r, fill):
        self.shapes.append(("rect", x, y, w, h, r, fill))

    def dot(self, cx, cy, r, fill):
        self.shapes.append(("ellipse", cx - r, cy - r, cx + r, cy + r, fill))

    def power(self, cx, cy, r, fill):
        self.shapes.append(("power", cx, cy, r, fill))

    def text(self, x, y, value, size, fill, bold=False, anchor="lm"):
        self.texts.append((x, y, value, size, bold, fill, anchor))

    def image(self, x, y, name, size):
        self.images.append((x, y, name, size))

    def hit(self, x, y, w, h, action):
        self.hits.append(((x, y, w, h), action))


def fit(value, size, bold, width, scale):
    f = font(size, bold, scale)
    if f.getlength(value) <= width * scale:
        return value
    while value and f.getlength(value + "…") > width * scale:
        value = value[:-1]
    return value + "…"


def switch(layout, x, cy, on, hover):
    fill = GOOD if on else (0, 0, 0, 0)
    layout.rect(x, cy - 8, 34, 16, 8, fill if on else (255, 255, 255, 60 if hover else 40))
    if not on:
        layout.rect(x + 1, cy - 7, 32, 14, 7, FOOTER)
    knob = 11 if hover else 10
    layout.dot(x + (26 if on else 8), cy, knob / 2, (20, 20, 20, 255) if on else MUTED)


def build(state, hover=None):
    """Lay out the flyout. Returns (layout, panel_height). Coordinates exclude MARGIN."""
    L, W = Layout(), WIDTH
    busy = state.get("busy")

    # Header: mark, title, "Full view" pill.
    L.image(16, 16, "switcher", 22)
    L.text(46, 27, "Account Switcher", 14, TEXT, bold=True)
    pill_w = 92
    L.rect(W - 16 - pill_w, 13, pill_w, 28, 6, PILL_HOVER if hover == "full" else PILL)
    L.text(W - 16 - pill_w + 13, 27, "Full view", 12, TEXT)
    L.text(W - 16 - 13, 26, "›", 16, TEXT, anchor="rm")
    L.hit(W - 16 - pill_w, 13, pill_w, 28, "full")
    y = 54

    for provider, title in PROVIDERS:
        accounts = [a for a in state["accounts"] if a["provider"] == provider]
        if not accounts:
            continue
        y += 6
        L.image(16, y + 4, provider, 14)
        L.text(37, y + 11, title.upper(), 11, ACCENT[provider], bold=True)
        tw = font(11, True, 1).getlength(title.upper())
        L.text(37 + tw + 7, y + 11, str(len(accounts)), 11, FAINT)
        y += 24
        for account in accounts:
            key = "swap:" + account["id"]
            top = y
            if hover == key and account["eligible"] and not account["active"]:
                L.rect(8, top, W - 16, ROW_H, 6, HOVER)
            elif account["active"]:
                L.rect(8, top, W - 16, ROW_H, 6, ACTIVE)
            if account["active"]:
                L.rect(10, top + 13, 3, ROW_H - 26, 1.5, GOOD)

            # Line 1: name, plan chip, status on the right.
            if not account["eligible"]:
                resets = min(w["resetsAt"] for w in account["windows"] if w["key"] in ("five_hour", "weekly") and w["used"] >= 100) \
                    if any(w["used"] >= 100 for w in account["windows"]) else None
                status, color = ("Limit · " + until(resets) if resets else "Limit reached"), BAD
            elif account["active"]:
                status, color = "In use", GOOD
            elif hover == key and not busy:
                status, color = "Switch", TEXT
            else:
                status, color = "", FAINT
            status_w = font(11, True, 1).getlength(status) + (12 if account["active"] else 0)
            name_x = 22
            chip = account.get("plan") or ""
            chip_w = font(10, True, 1).getlength(chip) + 12 if chip else 0
            name = fit(short_name(account), 13, True, W - name_x - 22 - status_w - chip_w - 16, 1)
            L.text(name_x, top + 17, name, 13, TEXT, bold=True)
            if chip:
                cx = name_x + font(13, True, 1).getlength(name) + 8
                accent = ACCENT[provider]
                L.rect(cx, top + 9, chip_w, 16, 4, accent[:3] + (38,))
                L.text(cx + 6, top + 17, chip, 10, accent, bold=True)
            if status:
                L.text(W - 22, top + 17, status, 11, color, bold=True, anchor="rm")
                if account["active"]:
                    L.dot(W - 22 - status_w + 4, top + 17, 3, GOOD)

            # Line 2: up to three compact meters.
            windows = account["windows"][:3]
            col_w = (W - 44 + 14) / len(windows)
            for i, window in enumerate(windows):
                left = remaining(window["used"])
                x0 = 22 + i * col_w
                ly = top + 38
                label = short_label(window)
                label_w = max(20, font(11, False, 1).getlength(label) + 7)
                L.text(x0, ly, label, 11, MUTED)
                bar_x, bar_w = x0 + label_w, col_w - label_w - 52
                L.rect(bar_x, ly - 2, bar_w, 4, 2, TRACK)
                if left > 0:
                    L.rect(bar_x, ly - 2, max(4, bar_w * left / 100), 4, 2, level_rgb(left))
                L.text(x0 + col_w - 14, ly, f"{left:.0f}%", 11, level_rgb(left), bold=True, anchor="rm")
            if account["eligible"] and not account["active"] and not busy:
                L.hit(8, top, W - 16, ROW_H, key)
            y += ROW_H + 2

    # Footer: switches and quit, PowerToys-style strip.
    y += 8
    footer_h = 48
    L.rect(0, y, W, footer_h, 0, FOOTER)
    L.rect(0, y, W, 1, 0, BORDER)
    cy = y + footer_h / 2
    x = 16
    for pref, label in (("autoSwap", "Auto swap"), ("afk", "AFK")):
        action = "toggle:" + pref
        switch(L, x, cy, state[pref], hover == action)
        L.text(x + 42, cy, label, 12, TEXT if not busy else MUTED)
        width = 42 + font(12, False, 1).getlength(label) + 18
        if not busy:
            L.hit(x - 4, cy - 14, width, 28, action)
        x += width + 4
    if hover == "quit":
        L.rect(W - 16 - 30, cy - 15, 30, 30, 6, PILL)
    L.power(W - 16 - 15, cy, 7, TEXT if hover == "quit" else MUTED)
    L.hit(W - 16 - 30, cy - 15, 30, 30, "quit")
    return L, y + footer_h


@lru_cache(maxsize=8)
def frame(height, scale):
    """Rounded panel mask and its soft shadow; depend only on size, so cached."""
    W, M, big = WIDTH, MARGIN, scale * SS
    full = (round((W + 2 * M) * scale), round((height + 2 * M) * scale))
    mask = Image.new("L", (round((W + 2 * M) * big), round((height + 2 * M) * big)), 0)
    ImageDraw.Draw(mask).rounded_rectangle((M * big, M * big, (M + W) * big - 1, (M + height) * big - 1), RADIUS * big, fill=255)
    mask = mask.resize(full, Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", full, (0, 0, 0, 0))
    shadow.putalpha(mask.point(lambda v: v * 110 // 255).filter(ImageFilter.GaussianBlur(12 * scale)))
    shadow = shadow.transform(full, Image.AFFINE, (1, 0, 0, 0, 1, -4 * scale))  # drop 4px
    return mask, shadow


def render(state, hover=None, scale=1.0):
    """Return (RGBA image including shadow margin, hits in logical px incl. margin)."""
    layout, height = build(state, hover)
    W, H, M = WIDTH, height, MARGIN
    full_w, full_h = round((W + 2 * M) * scale), round((H + 2 * M) * scale)
    big = scale * SS

    mask_size = (round((W + 2 * M) * big), round((H + 2 * M) * big))
    mask, shadow = frame(H, scale)
    shadow = shadow.copy()

    # Shapes at SS x on an opaque canvas (so translucent fills blend), then downsampled
    # and cut to the rounded panel shape.
    canvas = Image.new("RGB", mask_size, BG[:3])
    d = ImageDraw.Draw(canvas, "RGBA")

    def P(v):
        return v * big

    for op in layout.shapes:
        kind = op[0]
        if kind == "rect":
            _, x, y, w, h, r, fill = op
            box = (P(M + x), P(M + y), P(M + x + w) - 1, P(M + y + h) - 1)
            if r:
                d.rounded_rectangle(box, P(r), fill=fill)
            else:
                d.rectangle(box, fill=fill)
        elif kind == "ellipse":
            _, x1, y1, x2, y2, fill = op
            d.ellipse((P(M + x1), P(M + y1), P(M + x2), P(M + y2)), fill=fill)
        elif kind == "power":
            _, cx, cy, r, fill = op
            cx, cy = P(M + cx), P(M + cy)
            w = max(1, round(P(1.4)))
            d.arc((cx - P(r), cy - P(r) + P(1), cx + P(r), cy + P(r) + P(1)), 300, 240, fill=fill, width=w)
            d.line((cx, cy - P(r) - P(1), cx, cy + P(1)), fill=fill, width=w)
    d.rounded_rectangle((P(M), P(M), P(M + W) - 1, P(M + H) - 1), P(RADIUS), outline=BORDER, width=max(1, round(big)))
    panel = canvas.resize((full_w, full_h), Image.Resampling.LANCZOS).convert("RGBA")
    panel.putalpha(mask)

    image = shadow
    image.alpha_composite(panel)
    draw = ImageDraw.Draw(image)
    for x, y, name, size in layout.images:
        icon = asset(name, round(size * scale))
        image.alpha_composite(icon, (round((M + x) * scale), round((M + y) * scale)))
    for x, y, value, size, bold, fill, anchor in layout.texts:
        draw.text(((M + x) * scale, (M + y) * scale), value, font=font(size, bold, scale), fill=fill, anchor=anchor)

    hits = [((M + x, M + y, w, h), action) for (x, y, w, h), action in layout.hits]
    return image, hits


def hit_test(hits, x, y):
    """Action under a point in logical px (including margin), or None."""
    for (hx, hy, hw, hh), action in hits:
        if hx <= x < hx + hw and hy <= y < hy + hh:
            return action
    return None
