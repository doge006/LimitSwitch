"""Draws the tray flyout and its right-click menu as images plus clickable regions.

Pure Pillow; no windowing. Shapes are drawn at 2x on an opaque canvas and downsampled
(anti-aliased edges, correct blending); text is drawn at final size by FreeType.
Everything is in logical pixels times `scale`.
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

WIDTH = 404         # panel width
MENU_WIDTH = 232
MARGIN = 18         # transparent margin that holds the shadow
RADIUS = 8
ROW_H = 64
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


def display_name(account):
    return account.get("name") or account.get("email") or account["alias"]


def short_label(window):
    if window["key"] == "five_hour":
        return "5h"
    if window["key"] == "weekly":
        return "1w"
    if window["key"] == "monthly":
        return "30d"
    return window["label"].split(" · ")[-1]


def until(ts):
    m = max(0, int((ts - time.time()) / 60))
    if m >= 1440:
        return f"{m // 1440}d {m % 1440 // 60}h"
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def status_note(account):
    """Short text when usage could not be fetched, else None."""
    status = account.get("status") or ""
    if not status:
        return None
    if "sign in" in status.lower() or "missing" in status.lower():
        return "Sign in again"
    return "Stale"


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


def text_w(value, size, bold=False):
    return font(size, bold, 1).getlength(value)


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

    def icon(self, kind, cx, cy, r, fill):
        self.shapes.append((kind, cx, cy, r, fill))

    def text(self, x, y, value, size, fill, bold=False, anchor="lm"):
        self.texts.append((x, y, value, size, bold, fill, anchor))

    def image(self, x, y, name, size):
        self.images.append((x, y, name, size))

    def hit(self, x, y, w, h, action):
        self.hits.append(((x, y, w, h), action))


def fit(value, size, bold, width):
    if text_w(value, size, bold) <= width:
        return value
    while value and text_w(value + "…", size, bold) > width:
        value = value[:-1]
    return value + "…"


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(4))


def fade(color, t):
    return color[:3] + (round(color[3] * max(0.0, min(1.0, t))),)


def switch(layout, x, cy, pos, hover):
    """Toggle whose knob position (0..1) can be animated."""
    off_track = (255, 255, 255, 40 + round(20 * hover))
    layout.rect(x, cy - 8, 34, 16, 8, mix(off_track, GOOD, pos))
    if pos < 1:
        layout.rect(x + 1, cy - 7, 32, 14, 7, fade(FOOTER, 1 - pos))
    knob = 10 + hover
    layout.dot(x + 8 + 18 * pos, cy, knob / 2, mix(MUTED, (20, 20, 20, 255), pos))


def icon_button(layout, x, y, size, kind, action, hover_amount, active=False):
    t = max(hover_amount, 0.6 if active else 0.0)
    if t > 0:
        layout.rect(x, y, size, size, 6, fade(PILL_HOVER, t))
    layout.icon(kind, x + size / 2, y + size / 2, 6.5, mix(MUTED, TEXT, t))
    layout.hit(x, y, size, size, action)


def days_text(ts):
    seconds = ts - time.time()
    if seconds <= 0:
        return None
    days = int(seconds // 86400)
    return f"{days}d" if days >= 1 else f"{int(seconds // 3600)}h"


def subscription_text(account):
    """("Renews 12d" | "Ends 3d", color) or (None, None)."""
    sub = account.get("subscription") or {}
    when = days_text(sub["at"]) if sub.get("at") else None
    if not when:
        return None, None
    return (f"Ends {when}", WARN) if sub.get("ends") else (f"Renews {when}", MUTED)


def targets(state, hover=None):
    """Resting values of everything that animates, derived from state + hover."""
    fx = {("toggle", p): 1.0 if state[p] else 0.0 for p in ("autoSwap", "afk")}
    for a in state["accounts"]:
        fx[("active", a["id"])] = 1.0 if a["active"] else 0.0
        for w in a["windows"][:3]:
            fx[("bar", a["id"], w["key"])] = remaining(w["used"])
    if hover:
        fx[("hover", hover)] = 1.0
    return fx


DIM = 0.45


def dim_row(layout, marks):
    """Fade everything drawn for one row (limit reached)."""
    shapes, texts, _ = marks
    for i in range(shapes, len(layout.shapes)):
        op = list(layout.shapes[i])
        op[-1] = fade(op[-1], DIM)
        layout.shapes[i] = tuple(op)
    for i in range(texts, len(layout.texts)):
        x, y, value, size, bold, fill, anchor = layout.texts[i]
        layout.texts[i] = (x, y, value, size, bold, fade(fill, DIM), anchor)


def build(state, hover=None, pending=None, pinned=False, fx=None):
    """Lay out the flyout. Returns (layout, panel_height). Coordinates exclude MARGIN.

    fx holds in-between animation values (see targets()); missing keys use resting values.
    """
    L, W = Layout(), WIDTH
    busy = state.get("busy")
    rest = targets(state, hover)
    fx = {**rest, **(fx or {})}
    h = lambda action: fx.get(("hover", action), 0.0)

    # Header: mark, title, pop-out, "Full view".
    L.image(16, 16, "switcher", 22)
    L.text(46, 27, "Account Switcher", 14, TEXT, bold=True)
    pill_w = 92
    pill_x = W - 16 - pill_w
    L.rect(pill_x, 13, pill_w, 28, 6, mix(PILL, PILL_HOVER, h("full")))
    L.text(pill_x + 13, 27, "Full view", 12, TEXT)
    L.text(W - 16 - 13 + 2 * h("full"), 26, "›", 16, TEXT, anchor="rm")
    L.hit(pill_x, 13, pill_w, 28, "full")
    icon_button(L, pill_x - 34, 13, 28, "popin" if pinned else "popout", "pin", h("pin"), active=pinned)
    y = 54

    accounts_all = state["accounts"]
    if not accounts_all:
        y += 10
        L.text(W / 2, y + 12, "No accounts yet", 13, TEXT, bold=True, anchor="mm")
        L.text(W / 2, y + 34, "Sign in to Claude Code or Codex, or add one here.", 11, MUTED, anchor="mm")
        y += 54
        for i, (provider, title) in enumerate(PROVIDERS):
            bx = W / 2 - 124 + i * 128
            action = "add:" + provider
            L.rect(bx, y, 120, 30, 6, mix(PILL, PILL_HOVER, h(action)))
            L.image(bx + 12, y + 8, provider, 14)
            L.text(bx + 32, y + 15, f"Add {title}", 12, TEXT)
            L.hit(bx, y, 120, 30, action)
        y += 44

    for provider, title in PROVIDERS:
        accounts = [a for a in accounts_all if a["provider"] == provider]
        if not accounts:
            continue
        y += 6
        L.image(16, y + 4, provider, 14)
        L.text(37, y + 11, title.upper(), 11, ACCENT[provider], bold=True)
        L.text(37 + text_w(title.upper(), 11, True) + 7, y + 11, str(len(accounts)), 11, FAINT)
        y += 24
        for account in accounts:
            key = "swap:" + account["id"]
            top = y
            marks = (len(L.shapes), len(L.texts), len(L.images))
            switchable = account["eligible"] and not account["active"] and not busy and not pending
            act = fx.get(("active", account["id"]), 0.0)
            hov = h(key) if switchable else 0.0
            if act > 0 or hov > 0:
                L.rect(8, top, W - 16, ROW_H, 6, (255, 255, 255, round(ACTIVE[3] * act + HOVER[3] * hov)))
            if act > 0.01:  # green marker grows from the centre as the account becomes active
                bar_h = (ROW_H - 26) * act
                L.rect(10, top + ROW_H / 2 - bar_h / 2, 3, bar_h, 1.5, fade(GOOD, act))

            # Line 1, right to left: status, subscription; the name takes the rest.
            cy = top + 16
            right = W - 22
            if pending == account["id"]:
                status, color = "Switching…", TEXT
            elif account["active"]:
                status, color = "In use", fade(GOOD, max(act, 0.35))
            elif not account["eligible"]:
                status, color = "Limit", BAD
            elif switchable and hov > 0:
                status, color = "Switch", fade(TEXT, hov)
            else:
                status, color = "", FAINT
            if status:
                L.text(right, cy, status, 11, color, bold=True, anchor="rm")
                right -= text_w(status, 11, True)
                if account["active"] and pending != account["id"]:
                    L.dot(right - 6, cy, 3, fade(GOOD, max(act, 0.35)))
                    right -= 10
                right -= 12
            note = status_note(account)
            sub_text, sub_color = subscription_text(account)
            if note:
                L.text(right, cy, note, 11, WARN, anchor="rm")
                right -= text_w(note, 11) + 12
            elif sub_text:
                L.text(right, cy, sub_text, 11, sub_color, anchor="rm")
                right -= text_w(sub_text, 11) + 12
            chip = account.get("plan") or ""
            chip_w = text_w(chip, 10, True) + 12 if chip else 0
            name = fit(display_name(account), 13, True, right - 22 - (chip_w + 8 if chip else 0))
            L.text(22, cy, name, 13, TEXT, bold=True)
            if chip:
                cx = 22 + text_w(name, 13, True) + 8
                L.rect(cx, top + 8, chip_w, 16, 4, ACCENT[provider][:3] + (38,))
                L.text(cx + 6, cy, chip, 10, ACCENT[provider], bold=True)

            # Lines 2-3: up to three compact meters, each with its reset timer underneath.
            windows = account["windows"][:3]
            if not windows:
                L.text(22, top + 38, "Usage not loaded yet" if not account.get("status") else account["status"], 11, FAINT)
            else:
                col_w = (W - 44 + 14) / len(windows)
                for i, window in enumerate(windows):
                    left = fx.get(("bar", account["id"], window["key"]), remaining(window["used"]))
                    shown = remaining(window["used"])
                    x0 = 22 + i * col_w
                    ly = top + 36
                    label = short_label(window)
                    label_w = max(20, text_w(label, 11) + 7)
                    L.text(x0, ly, label, 11, MUTED)
                    bar_x, bar_w = x0 + label_w, col_w - label_w - 52
                    L.rect(bar_x, ly - 2, bar_w, 4, 2, TRACK)
                    if left > 0.5:
                        L.rect(bar_x, ly - 2, max(4, bar_w * left / 100), 4, 2, level_rgb(left))
                    L.text(x0 + col_w - 14, ly, f"{shown:.0f}%", 11, level_rgb(shown), bold=True, anchor="rm")
                    if window.get("resetsAt"):
                        L.text(bar_x, ly + 14, "in " + until(window["resetsAt"]), 10, FAINT)
            if switchable:
                L.hit(8, top, W - 16, ROW_H, key)
            if not account["eligible"]:
                dim_row(L, marks)  # greyed out, like the full view
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
        switch(L, x, cy, fx[("toggle", pref)], h(action))
        L.text(x + 42, cy, label, 12, TEXT if not busy else MUTED)
        width = 42 + text_w(label, 12) + 18
        if not busy:
            L.hit(x - 4, cy - 14, width, 28, action)
        x += width + 4
    icon_button(L, W - 16 - 30, cy - 15, 30, "power", "quit", h("quit"))
    return L, y + footer_h


def build_menu(items, hover=None, fx=None):
    """Right-click menu. items: dicts {action, label, checked?, bold?, enabled?} or "-"."""
    L, W = Layout(), MENU_WIDTH
    fx = fx if fx is not None else ({("hover", hover): 1.0} if hover else {})
    y = 5
    for item in items:
        if item == "-":
            L.rect(10, y + 4, W - 20, 1, 0, BORDER)
            y += 9
            continue
        enabled = item.get("enabled", True)
        amount = fx.get(("hover", item["action"]), 0.0)
        if amount > 0 and enabled:
            L.rect(5, y, W - 10, 32, 5, fade((255, 255, 255, 16), amount))
        if item.get("checked"):
            L.icon("check", 20, y + 16, 5, TEXT if enabled else FAINT)
        L.text(36, y + 16, item["label"], 12, TEXT if enabled else FAINT, bold=item.get("bold", False))
        if enabled:
            L.hit(5, y, W - 10, 32, item["action"])
        y += 32
    return L, y + 5


@lru_cache(maxsize=8)
def frame(width, height, scale):
    """Rounded panel mask and its soft shadow; depend only on size, so cached."""
    M, big = MARGIN, scale * SS
    full = (round((width + 2 * M) * scale), round((height + 2 * M) * scale))
    mask = Image.new("L", (round((width + 2 * M) * big), round((height + 2 * M) * big)), 0)
    ImageDraw.Draw(mask).rounded_rectangle((M * big, M * big, (M + width) * big - 1, (M + height) * big - 1), RADIUS * big, fill=255)
    mask = mask.resize(full, Image.Resampling.LANCZOS)
    shadow = Image.new("RGBA", full, (0, 0, 0, 0))
    shadow.putalpha(mask.point(lambda v: v * 110 // 255).filter(ImageFilter.GaussianBlur(12 * scale)))
    shadow = shadow.transform(full, Image.AFFINE, (1, 0, 0, 0, 1, -4 * scale))  # drop 4px
    return mask, shadow


def paint(layout, width, height, scale):
    """Rasterise a layout. Returns (RGBA image incl. shadow margin, hits in logical px incl. margin)."""
    M, big = MARGIN, scale * SS
    full = (round((width + 2 * M) * scale), round((height + 2 * M) * scale))
    mask, shadow = frame(width, height, scale)
    canvas = Image.new("RGB", (round((width + 2 * M) * big), round((height + 2 * M) * big)), BG[:3])
    d = ImageDraw.Draw(canvas, "RGBA")

    def P(v):
        return v * big

    line = max(1, round(P(1.4)))
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
        else:
            _, cx, cy, r, fill = op
            cx, cy, r = P(M + cx), P(M + cy), P(r)
            if kind == "power":
                d.arc((cx - r, cy - r + P(1), cx + r, cy + r + P(1)), 300, 240, fill=fill, width=line)
                d.line((cx, cy - r - P(1), cx, cy + P(1)), fill=fill, width=line)
            elif kind == "clock":
                d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=fill, width=line)
                d.line((cx, cy - r * .55, cx, cy, cx + r * .45, cy + r * .3), fill=fill, width=line, joint="curve")
            elif kind == "check":
                d.line((cx - r, cy, cx - r * .3, cy + r * .7, cx + r, cy - r * .7), fill=fill, width=line, joint="curve")
            elif kind in ("popout", "popin"):
                # A window with an arrow leaving it (pop out) or entering it (pop in).
                d.rounded_rectangle((cx - r, cy - r * .6, cx + r * .6, cy + r), P(1.5), outline=fill, width=line)
                if kind == "popout":
                    d.line((cx - r * .1, cy + r * .1, cx + r, cy - r), fill=fill, width=line)
                    d.line((cx + r * .25, cy - r, cx + r, cy - r, cx + r, cy - r * .25), fill=fill, width=line, joint="curve")
                else:
                    d.line((cx + r, cy - r, cx + r * .05, cy - r * .05), fill=fill, width=line)
                    d.line((cx - r * .05, cy - r * .7, cx + r * .05, cy - r * .05, cx + r * .7, cy + r * .05), fill=fill, width=line, joint="curve")
    d.rounded_rectangle((P(M), P(M), P(M + width) - 1, P(M + height) - 1), P(RADIUS), outline=BORDER, width=max(1, round(big)))
    panel = canvas.reduce(SS) if canvas.size == (full[0] * SS, full[1] * SS) else canvas.resize(full, Image.Resampling.BOX)
    # Images and text go on the opaque panel (so translucent text blends), then the
    # rounded shape is cut and the result laid over the cached shadow.
    for x, y, name, size in layout.images:
        icon = asset(name, round(size * scale))
        panel.paste(icon, (round((M + x) * scale), round((M + y) * scale)), icon)
    draw = ImageDraw.Draw(panel, "RGBA")
    for x, y, value, size, bold, fill, anchor in layout.texts:
        if len(fill) == 4 and fill[3] < 255:
            # Pillow ignores the ink's alpha for text, so fade by mixing with the panel colour.
            t = fill[3] / 255
            fill = tuple(round(BG[i] * (1 - t) + fill[i] * t) for i in range(3)) + (255,)
        draw.text(((M + x) * scale, (M + y) * scale), value, font=font(size, bold, scale), fill=fill, anchor=anchor)
    panel = panel.convert("RGBA")
    panel.putalpha(mask)
    image = shadow.copy()
    image.alpha_composite(panel)
    return image, [((M + x, M + y, w, h), action) for (x, y, w, h), action in layout.hits]


def render(state, hover=None, scale=1.0, pending=None, pinned=False, fx=None):
    layout, height = build(state, hover, pending, pinned, fx)
    return paint(layout, WIDTH, height, scale)


def render_menu(items, hover=None, scale=1.0, fx=None):
    layout, height = build_menu(items, hover, fx)
    return paint(layout, MENU_WIDTH, height, scale)


def hit_test(hits, x, y):
    """Action under a point in logical px (including margin), or None."""
    for (hx, hy, hw, hh), action in hits:
        if hx <= x < hx + hw and hy <= y < hy + hh:
            return action
    return None


def header_height():
    """Pinned flyouts can be dragged by the header strip (above the first section)."""
    return MARGIN + 50
