"""Draws the full view (the app's main window) with Pillow: no browser, same look as before.

Pure drawing, no windowing: each platform's host (Win32, AppKit, Tk) shows the frames and passes
input to fullview.FullView. The page is split into tiles (the header, each provider heading, each
account card) that are drawn once and cached; hovering or a change to one account redraws only
that tile, and scrolling only re-composes cached tiles. Shapes are anti-aliased with cached masks
instead of drawing the whole window at 2x. Everything is laid out in logical pixels times `scale`.
"""
from functools import lru_cache
import json
import sys
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from . import flyout_render as fr

# Colours (the web full view's palette)
BG = (22, 22, 22)
SURFACE = (32, 32, 32)
SURFACE_2 = (39, 39, 39)
SURFACE_3 = (46, 46, 46)
LINE = (255, 255, 255, 18)
LINE_STRONG = (255, 255, 255, 33)
TEXT = (243, 243, 243)
MUTED = (163, 163, 163)
FAINT = (149, 149, 149)
TRACK = (255, 255, 255, 23)
GOOD, WARN, BAD = (76, 195, 138), (229, 181, 74), (239, 106, 91)
ACCENT = {"claude": (224, 138, 104), "codex": (162, 149, 247)}
ON_ACCENT = (22, 22, 22)
FOCUS = (138, 180, 255)
PROVIDERS = (("claude", "Claude", "Claude Code"), ("codex", "Codex", "Codex CLI & app"))

PAD = 28           # page padding
MAX_W = 1180       # content max width
GAP = 14           # between cards
CARD_MIN = 380     # narrowest card before the grid drops a column
RADIUS = 14
SHADOW = 18        # room around a card for its shadow
TOPBAR_H = 66
GROUP_HEAD_H = 34
SCROLL_STEP = 64


# ---------- small helpers ----------
def remaining(used):
    return max(0, min(100, 100 - used))


def level(left):
    return GOOD if left > 30 else WARN if left > 10 else BAD


def blend(color, bg, alpha):
    """An RGB colour at `alpha` (0..1) over bg: Pillow draws text without the ink's alpha."""
    return tuple(round(bg[i] * (1 - alpha) + color[i] * alpha) for i in range(3))


def over(bg, rgba):
    return blend(rgba[:3], bg, rgba[3] / 255)


def redact(email):
    """d**********@gmail.com: the first letter and the domain stay."""
    user, _, domain = (email or "").partition("@")
    if not user:
        return ""
    return user[0] + "*" * max(1, len(user) - 1) + ("@" + domain if domain else "")


@lru_cache(maxsize=1)
def clock_12h():
    """Does this user's clock show AM/PM? (Windows: the short time format; elsewhere the locale.)"""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:
                return "h" in winreg.QueryValueEx(key, "sShortTime")[0]
        except OSError:
            return False
    try:
        import locale
        locale.setlocale(locale.LC_TIME, "")
        return bool(time.strftime("%p", time.localtime(0)))
    except Exception:
        return False


def clock(ts):
    t = time.localtime(ts)
    if clock_12h():
        return time.strftime("%I:%M %p", t).lstrip("0")
    return time.strftime("%H:%M", t)


def relative(ts):
    m = max(0, round((ts - time.time()) / 60))
    if m < 1:
        return "now"
    if m < 60:
        return f"in {m}m"
    if m < 1440:
        return f"in {m // 60}h {m % 60}m"
    return f"in {m // 1440}d {m % 1440 // 60}h"


def absolute(ts):
    same_day = time.localtime(ts)[:3] == time.localtime()[:3]
    return clock(ts) if same_day else time.strftime("%a ", time.localtime(ts)) + clock(ts)


def reset_text(ts):
    return f"Resets {absolute(ts)} · {relative(ts)}" if ts else "Reset time not reported"


def date_text(ts):
    t = time.localtime(ts)
    return time.strftime("%b ", t) + str(t.tm_mday)


def subscription_text(account):
    """"Renews Oct 14 · in 18d" / "Ends Oct 14 · in 3d", or ''."""
    sub = account.get("subscription") or {}
    if not sub.get("at") or sub["at"] < time.time() - 86400:
        return ""
    days = int((sub["at"] - time.time()) // 86400)
    when = f"in {days}d" if days >= 1 else "today"
    return f"{'Ends' if sub.get('ends') else 'Renews'} {'~' if sub.get('estimated') else ''}{date_text(sub['at'])} · {when}"


def credits_items(account):
    c = account.get("credits")
    if not c:
        return []
    items = []
    main = None
    if c.get("kind") == "credits":
        if c.get("unlimited"):
            main = ("Credits", "unlimited")
        elif isinstance(c.get("balance"), (int, float)):
            main = ("Credits", f"{c['balance']:,.2f}".rstrip("0").rstrip("."))
        elif c.get("enabled"):
            main = ("Credits", "available")
    elif c.get("kind") != "none":
        if not c.get("enabled"):
            main = ("Extra usage", "off")
        elif isinstance(c.get("limit"), (int, float)) and isinstance(c.get("used"), (int, float)):
            main = ("Extra usage", f"{max(0, c['limit'] - c['used']):,g} of {c['limit']:,g} left")
        else:
            main = ("Extra usage", "on")
    if main:
        items.append(main)
    if isinstance(c.get("resets"), int):
        items.append(("Usage limit resets", str(c["resets"])))
    return items


def display_name(account):
    return account.get("name") or account.get("email") or account.get("alias", "")


# ---------- anti-aliased shapes from cached masks ----------
@lru_cache(maxsize=256)
def rr_mask(w, h, r):
    """Rounded-rectangle coverage mask (w, h, r in device px), drawn at 4x and reduced."""
    k = 4
    big = Image.new("L", (max(1, w * k), max(1, h * k)), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, w * k - 1, h * k - 1), max(0, r * k), fill=255)
    return big.reduce(k) if w and h else big


@lru_cache(maxsize=128)
def ring_mask(w, h, r, width):
    """A rounded-rectangle outline of `width` device px."""
    hole = Image.new("L", (w, h), 0)
    hole.paste(rr_mask(max(1, w - 2 * width), max(1, h - 2 * width), max(0, r - width)), (width, width))
    return ImageChops.subtract(rr_mask(w, h, r), hole)


@lru_cache(maxsize=256)
def rr_alpha(w, h, r, alpha):
    mask = rr_mask(w, h, r)
    return mask.point(lambda v: v * alpha // 255) if alpha < 255 else mask


@lru_cache(maxsize=128)
def ring_alpha(w, h, r, width, alpha):
    mask = ring_mask(w, h, r, width)
    return mask.point(lambda v: v * alpha // 255) if alpha < 255 else mask


class Canvas:
    """Draws in logical px on an RGB(A) image of scale x that size."""

    def __init__(self, image, scale, bg):
        self.image, self.s, self.bg = image, scale, bg
        self.draw = ImageDraw.Draw(image)

    def px(self, v):
        return round(v * self.s)

    def box(self, x, y, w, h):
        x0, y0 = self.px(x), self.px(y)
        return x0, y0, max(1, self.px(x + w) - x0), max(1, self.px(y + h) - y0)

    def rect(self, x, y, w, h, r, fill):
        x0, y0, pw, ph = self.box(x, y, w, h)
        alpha = fill[3] if len(fill) == 4 else 255
        mask = rr_alpha(pw, ph, self.px(r), alpha)
        self.image.paste(fill[:3], (x0, y0), mask)

    def outline(self, x, y, w, h, r, color, width=1):
        x0, y0, pw, ph = self.box(x, y, w, h)
        alpha = color[3] if len(color) == 4 else 255
        mask = ring_alpha(pw, ph, self.px(r), max(1, round(width * self.s)), alpha)
        self.image.paste(color[:3], (x0, y0), mask)

    def dot(self, cx, cy, r, fill):
        self.rect(cx - r, cy - r, 2 * r, 2 * r, r, fill)

    def line(self, x, y, w, color):
        """A hairline across (1 device px)."""
        x0, y0 = self.px(x), self.px(y)
        self.image.paste(over(self.bg, color) if len(color) == 4 else color, (x0, y0, self.px(x + w), y0 + max(1, round(self.s))))

    def text(self, x, y, value, size, fill, bold=False, anchor="ls", bg=None):
        if len(fill) == 4:
            fill = over(bg or self.bg, fill)
        self.draw.text((x * self.s, y * self.s), value, font=fr.font(size, bold, self.s), fill=fill, anchor=anchor)

    def image_at(self, x, y, name, size):
        icon = fr.asset(name, self.px(size))
        self.image.paste(icon, (self.px(x), self.px(y)), icon)

    def glyph(self, kind, cx, cy, size, color):
        icon = glyph(kind, self.px(size), color)
        self.image.paste(icon, (self.px(cx) - icon.width // 2, self.px(cy) - icon.height // 2), icon)


@lru_cache(maxsize=64)
def glyph(kind, px, color):
    """Small line icons (caret, plus, refresh, check), drawn at 4x and reduced."""
    k = 4
    size = px * k
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    w = max(k, round(size * 0.1))
    c = color + (255,) if len(color) == 3 else color
    s = size
    if kind == "caret":
        d.line((s * .25, s * .38, s * .5, s * .63, s * .75, s * .38), fill=c, width=w, joint="curve")
    elif kind == "plus":
        d.line((s * .5, s * .2, s * .5, s * .8), fill=c, width=w)
        d.line((s * .2, s * .5, s * .8, s * .5), fill=c, width=w)
    elif kind == "refresh":
        d.arc((s * .18, s * .18, s * .82, s * .82), 40, 330, fill=c, width=w)
        d.line((s * .82, s * .12, s * .82, s * .42, s * .52, s * .42), fill=c, width=w, joint="curve")
    elif kind == "check":
        d.line((s * .2, s * .52, s * .42, s * .72, s * .8, s * .3), fill=c, width=w, joint="curve")
    elif kind == "left":
        d.line((s * .6, s * .25, s * .35, s * .5, s * .6, s * .75), fill=c, width=w, joint="curve")
    elif kind == "right":
        d.line((s * .4, s * .25, s * .65, s * .5, s * .4, s * .75), fill=c, width=w, joint="curve")
    return image.reduce(k)


@lru_cache(maxsize=16)
def shadow(w, h, scale, strength=1.0):
    """A card's soft shadow (0 1px 2px .3, 0 8px 24px .22) on a transparent image with SHADOW margin."""
    m = round(SHADOW * scale)
    full = (round(w * scale) + 2 * m, round(h * scale) + 2 * m)
    base = Image.new("L", full, 0)
    base.paste(rr_mask(round(w * scale), round(h * scale), round(RADIUS * scale)), (m, m))
    soft = base.point(lambda v: round(v * .22 * strength)).filter(ImageFilter.GaussianBlur(10 * scale))
    near = base.point(lambda v: round(v * .3 * strength)).filter(ImageFilter.GaussianBlur(1 * scale))
    out = Image.new("L", full, 0)
    out.paste(soft, (0, round(7 * scale)))
    lifted = Image.new("L", full, 0)
    lifted.paste(near, (0, round(scale)))
    out = ImageChops.lighter(out, lifted)
    layer = Image.new("RGBA", full, (0, 0, 0, 0))
    layer.putalpha(out)
    return layer


# ---------- layout: where everything goes (logical px, page coordinates) ----------
def columns_for(width):
    inner = min(MAX_W, width - 2 * PAD)
    return max(1, int((inner + GAP) // (CARD_MIN + GAP))), inner


def identity_height(account, name_mode):
    h = 22  # the name
    if name_mode:
        h += 18  # the email under it
    if account.get("plan"):
        h += 22
    return max(38, h)


def card_height(account, name_mode):
    h = 16 + identity_height(account, name_mode) + 14
    windows = account.get("windows") or []
    h += len(windows) * 50 + max(0, len(windows) - 1) * 11 if windows else 28
    if credits_items(account):
        h += 11 + 18
    return h + 14 + 13 + 32 + 14


def layout(state, width):
    """[(kind, key, x, y, w, h, data)] for the page, and its total height."""
    cols, inner = columns_for(width)
    left = (width - inner) / 2
    items = [("topbar", "topbar", left, PAD, inner, TOPBAR_H, None)]
    y = PAD + TOPBAR_H + 22
    accounts = state.get("accounts") or []
    if not accounts:
        items.append(("empty", "empty", left, y, inner, 170, None))
        return items, y + 170 + PAD
    card_w = (inner - (cols - 1) * GAP) / cols
    name_mode = bool(state.get("nameMode"))
    for provider, title, caption in PROVIDERS:
        group = [a for a in accounts if a["provider"] == provider]
        if not group:
            continue
        items.append(("group", "group:" + provider, left, y, inner, GROUP_HEAD_H, (provider, title, caption, len(group))))
        y += GROUP_HEAD_H + 12
        for row in range(0, len(group), cols):
            chunk = group[row:row + cols]
            h = max(card_height(a, name_mode) for a in chunk)
            for i, account in enumerate(chunk):
                items.append(("card", "card:" + account["id"], left + i * (card_w + GAP), y, card_w, h, account))
            y += h + GAP
        y += 26 - GAP
    return items, y - 26 + PAD


# ---------- tiles ----------
class Tile:
    def __init__(self, image, hits, margin=0):
        self.image, self.hits, self.margin = image, hits, margin


def card_key(account, ui, name_mode, live, locked):
    """Everything a card's look depends on, so a cached tile is reused until one of them changes."""
    aid = account["id"]
    editing = ui.editing if ui.editing and ui.editing[0] == aid else None
    return json.dumps([account, ui.hover if ui.hover and ui.hover_card == aid else None, ui.hover_card == aid,
                       ui.pending == aid, ui.confirm == aid, editing, aid in ui.revealed, name_mode, live, locked,
                       int(time.time() // 60)], sort_keys=True, default=str)


def draw_card(account, w, h, scale, ui, name_mode, live, locked):
    """One account card: returns a Tile (RGBA with shadow margin) with hits relative to the card."""
    provider, aid = account["provider"], account["id"]
    accent = ACCENT[provider]
    active, eligible = account.get("active"), account.get("eligible", True)
    hovered = ui.hover_card == aid
    hover = ui.hover if hovered else None
    m = round(SHADOW * scale)
    body = Image.new("RGB", (round(w * scale), round(h * scale)), SURFACE)
    c = Canvas(body, scale, SURFACE)
    hits = []

    def hit(x, y, bw, bh, action, cursor="hand"):
        hits.append(((x, y, bw, bh), action, cursor))

    # Head: avatar, identity, renewal + badge
    x, y = 18, 16
    c.rect(x, y, 38, 38, 11, accent + (36,))
    c.image_at(x + 8, y + 8, provider, 22)
    ix = x + 38 + 12
    side_w = 0
    sub = subscription_text(account)
    renew = sub or ("Set renewal date" if live else "")
    badge = "In use" if active else ("Limit reached" if not eligible else "")
    for value, size in ((renew, 11.5), (badge, 12)):
        if value:
            side_w = max(side_w, fr.text_w(value, size, size == 12) + (13 if value == badge else 0))
    name_w = w - ix - 18 - (side_w + 12 if side_w else 0)
    iy = y + 15
    if name_mode:
        editing = ui.editing if ui.editing and ui.editing[0] == aid else None
        label = account.get("label") or ""
        box_hover = hover == "name:" + aid
        if editing:
            c.rect(ix - 6, iy - 16, min(260, name_w + 6), 24, 6, SURFACE_2 + (255,))
            c.outline(ix - 6, iy - 16, min(260, name_w + 6), 24, 6, FOCUS + (255,))
            text, caret = editing[1], editing[2]
            shown = text or ""
            if editing[3] and shown:  # all selected
                c.rect(ix, iy - 12, fr.text_w(shown, 15, True), 17, 3, FOCUS + (90,))
            c.text(ix, iy, shown, 15, TEXT if shown else MUTED, True, bg=SURFACE_2)
            cx = ix + fr.text_w(shown[:caret], 15, True)
            c.rect(cx, iy - 13, 1.2, 17, 0, TEXT + (255,))
        else:
            if box_hover:
                c.outline(ix - 6, iy - 16, min(260, name_w + 6), 24, 6, LINE_STRONG)
            if label:
                c.text(ix, iy, fr.fit(label, 15, True, name_w), 15, TEXT, True)
            else:
                c.text(ix, iy, "Name this account", 15, MUTED)
        hit(ix - 6, iy - 16, min(260, name_w + 6), 24, "name:" + aid, "text")
        iy += 18
        email = account.get("email") or ""
        shown = email if aid in ui.revealed else redact(email)
        if shown:
            color = TEXT if hover == "email:" + aid else MUTED
            c.text(ix, iy, fr.fit(shown, 12, False, name_w), 12, color)
            hit(ix, iy - 12, min(name_w, fr.text_w(shown, 12) + 4), 16, "email:" + aid)
    else:
        c.text(ix, iy, fr.fit(display_name(account), 15, True, name_w), 15, TEXT, True)
    if account.get("plan"):
        py = iy + 6
        pw = fr.text_w(account["plan"], 11, True) + 14
        c.rect(ix, py, pw, 17, 6, accent + (36,))
        c.text(ix + 7, py + 12.5, account["plan"], 11, accent, True, bg=over(SURFACE, accent + (36,)))
    right = w - 18
    if renew:
        rcolor = WARN if sub and (account.get("subscription") or {}).get("ends") else (MUTED if sub else FAINT)
        rhover = hover == "renew:" + aid
        rw = fr.text_w(renew, 11.5)
        if rhover and live:
            c.rect(right - rw - 4, y + 1, rw + 8, 17, 5, SURFACE_3 + (255,))
            rcolor = TEXT
        c.text(right, y + 14, renew, 11.5, rcolor, anchor="rs", bg=SURFACE_3 if rhover and live else SURFACE)
        if live:
            hit(right - rw - 4, y + 1, rw + 8, 17, "renew:" + aid)
    if badge:
        bcolor = GOOD if active else BAD
        c.text(right, y + 33, badge, 12, bcolor, True, anchor="rs")
        c.dot(right - fr.text_w(badge, 12, True) - 8, y + 29, 3.5, bcolor)

    # Usage windows
    y = 16 + identity_height(account, name_mode) + 14
    windows = account.get("windows") or []
    if not windows:
        c.text(18, y + 18, "Usage not loaded yet", 12.5, FAINT)
        y += 28
    for i, win in enumerate(windows):
        left = remaining(win.get("used", 0))
        col = level(left)
        c.text(18, y + 14, win.get("label", ""), 13, TEXT)
        c.text(w - 18, y + 14, " left", 12, MUTED, anchor="rs")
        c.text(w - 18 - fr.text_w(" left", 12), y + 14, f"{left:.0f}%", 13, col, True, anchor="rs")
        c.rect(18, y + 24, w - 36, 6, 3, TRACK)
        if left > 0.3:
            c.rect(18, y + 24, max(6, (w - 36) * left / 100), 6, 3, col + (255,))
        c.text(18, y + 46, reset_text(win.get("resetsAt")), 11.5, FAINT)
        y += 50 + 11
    if windows:
        y -= 11
    items = credits_items(account)
    if items:
        y += 11
        cx = 18
        for i, (label, value) in enumerate(items):
            if i == len(items) - 1 and len(items) > 1:  # the last one sits at the right
                total = fr.text_w(label + ": ", 12) + fr.text_w(value, 12)
                cx = w - 18 - total
            c.text(cx, y + 13, label + ": ", 12, MUTED)
            c.text(cx + fr.text_w(label + ": ", 12), y + 13, value, 12, TEXT)
            cx += fr.text_w(label + ": " + value, 12) + 12

    # Foot: hint on the left, Remove and the swap button on the right
    fy = h - 14 - 32
    c.line(18, fy - 13, w - 36, LINE)
    switching = ui.pending == aid
    if ui.confirm == aid:
        c.text(18, fy + 20, "Remove this account?", 12.5, TEXT)
        bx = w - 18 - 84
        danger = hover == "remove-yes:" + aid
        c.rect(bx, fy, 84, 32, 8, (BAD if not danger else blend(BAD, (255, 255, 255), .12)) + (255,))
        c.text(bx + 42, fy + 16, "Remove", 13, ON_ACCENT, True, anchor="mm", bg=BAD)
        hit(bx, fy, 84, 32, "remove-yes:" + aid)
        bx -= 84
        if hover == "remove-no:" + aid:
            c.rect(bx, fy, 78, 32, 8, SURFACE_3 + (255,))
        c.text(bx + 39, fy + 16, "Cancel", 13, MUTED if hover != "remove-no:" + aid else TEXT, anchor="mm",
               bg=SURFACE_3 if hover == "remove-no:" + aid else SURFACE)
        hit(bx, fy, 78, 32, "remove-no:" + aid)
    else:
        status = account.get("status") or ""
        relogin = live and any(s in status.lower() for s in ("sign in", "expired", "missing"))
        if relogin:
            signing = provider in (ui.signing_in or ())
            label = "Login expired · Sign in again"
            color = TEXT if hover == "relogin:" + aid and not signing else (FAINT if signing else TEXT)
            c.text(18, fy + 20, label, 12, color)
            lw = fr.text_w(label, 12)
            c.line(18, fy + 22, lw, color + (160,))
            if not signing:
                hit(18, fy + 4, lw, 22, "relogin:" + aid)
        else:
            hint = status or ("All sessions use this account" if active else "Waiting for reset" if not eligible else "")
            c.text(18, fy + 20, fr.fit(hint, 12, False, w - 36 - 124 - 90), 12, WARN if status else FAINT)
        bw, bx = 124, w - 18 - 124
        if active and not switching:
            c.text(bx + bw / 2, fy + 16, "In use", 13, accent, True, anchor="mm")
        elif switching:
            c.rect(bx, fy, bw, 32, 8, accent + (220,))
            c.text(bx + bw / 2, fy + 16, "Switching…", 13, ON_ACCENT, True, anchor="mm", bg=over(SURFACE, accent + (220,)))
        elif not eligible:
            c.rect(bx, fy, bw, 32, 8, SURFACE_2 + (102,))
            c.outline(bx, fy, bw, 32, 8, LINE_STRONG[:3] + (40,))
            c.text(bx + bw / 2, fy + 16, "Limit reached", 13, blend(TEXT, SURFACE, .4), anchor="mm")
        else:
            hot = hover == "swap:" + aid and not locked
            fill = blend(accent, (255, 255, 255), .08) if hot else accent
            if locked:
                fill = blend(accent, SURFACE, .45)
            c.rect(bx, fy - (1 if hot else 0), bw, 32, 8, fill + (255,))
            c.text(bx + bw / 2, fy + 16 - (1 if hot else 0), "Swap to this", 13, ON_ACCENT, True, anchor="mm", bg=fill)
            if not locked:
                hit(bx, fy, bw, 32, "swap:" + aid)
        if live and not active and hovered and not switching:
            rx = bx - 6 - 78
            hot = hover == "remove:" + aid
            if hot:
                c.rect(rx, fy, 78, 32, 8, SURFACE_3 + (255,))
            c.text(rx + 39, fy + 16, "Remove", 13, TEXT if hot else MUTED, anchor="mm", bg=SURFACE_3 if hot else SURFACE)
            if not locked:
                hit(rx, fy, 78, 32, "remove:" + aid)

    # Shape: rounded card, border (accent when in use, stronger on hover), shadow, dimmed when spent
    card = body.convert("RGBA")
    border = accent + (115,) if active else (LINE_STRONG if hovered else LINE)
    ring = ring_alpha(body.width, body.height, round(RADIUS * scale), max(1, round(scale)), border[3])
    card.paste(border[:3], (0, 0), ring)
    mask = rr_alpha(body.width, body.height, round(RADIUS * scale), 184 if not eligible and not active else 255)  # .72 when spent
    card.putalpha(mask)
    tile = shadow(w, h, scale).copy()
    tile.alpha_composite(card, (m, m))
    return Tile(tile, hits, m)


def draw_topbar(state, w, scale, ui):
    image = Image.new("RGBA", (round(w * scale), round(TOPBAR_H * scale)), (0, 0, 0, 0))
    c = Canvas(image, scale, BG)
    hits = []
    hover = ui.hover
    mark = fr.asset("switcher", c.px(44))
    rounded = Image.new("RGBA", mark.size, (0, 0, 0, 0))
    rounded.paste(mark, (0, 0), Image.composite(mark.getchannel("A"), Image.new("L", mark.size, 0), rr_mask(*mark.size, c.px(12))))
    image.alpha_composite(rounded, (0, c.px(11)))
    c.text(58, 30, "Account Switcher", 22, TEXT, True)
    c.text(58, 51, "Every Claude and Codex limit, at a glance.", 13, MUTED)
    live = state.get("mode") == "live"
    x = w
    # Refresh
    x -= 36
    hot = hover == "refresh"
    c.rect(x, 15, 36, 36, 9, (SURFACE_3 if hot else SURFACE) + (255,))
    c.outline(x, 15, 36, 36, 9, LINE)
    c.glyph("refresh", x + 18, 33, 17, TEXT if hot else MUTED)
    hits.append(((x, 15, 36, 36), "refresh", "hand"))
    # Add account
    if live:
        label = "Add account"
        bw = fr.text_w(label, 13) + 14 + 22
        x -= 10 + bw
        hot = hover == "add" or ui.menu == "add"
        c.rect(x, 17, bw, 32, 8, (SURFACE_3 if hot else SURFACE_2) + (255,))
        c.outline(x, 17, bw, 32, 8, LINE_STRONG)
        c.glyph("plus", x + 20, 33, 14, TEXT)
        c.text(x + 31, 33, label, 13, TEXT, anchor="lm", bg=SURFACE_3 if hot else SURFACE_2)
        hits.append(((x, 17, bw, 32), "add", "hand"))
    # Settings, with the automation state pill
    busy = state.get("busy")
    pill = "Working…" if busy else "AFK armed" if state.get("afk") else "Watching" if state.get("autoSwap") else "Manual"
    pill_bg, pill_fg = ((229, 181, 74, 38), WARN) if busy else ((76, 195, 138, 36), GOOD) if (state.get("afk") or state.get("autoSwap")) else (SURFACE_3 + (255,), MUTED)
    pw = fr.text_w(pill, 11, True) + 18
    bw = 14 + fr.text_w("Settings", 13) + 8 + pw + 6 + 14 + 12
    x -= 10 + bw
    hot = hover == "settings" or ui.menu == "settings"
    base = SURFACE_3 if hot else SURFACE_2
    c.rect(x, 17, bw, 32, 8, base + (255,))
    c.outline(x, 17, bw, 32, 8, LINE_STRONG)
    c.text(x + 14, 33, "Settings", 13, TEXT, anchor="lm", bg=base)
    px_ = x + 14 + fr.text_w("Settings", 13) + 8
    c.rect(px_, 24, pw, 18, 9, pill_bg)
    c.text(px_ + pw / 2, 33, pill, 11, pill_fg, True, anchor="mm", bg=over(base, pill_bg))
    c.glyph("caret", x + bw - 19, 33, 14, MUTED)
    hits.append(((x, 17, bw, 32), "settings", "hand"))
    ui.anchors["settings"] = (x, bw)
    ui.anchors["add"] = (x + bw + 10, fr.text_w("Add account", 13) + 36)
    return Tile(image, hits)


def draw_group(data, w, scale):
    provider, title, caption, count = data
    image = Image.new("RGBA", (round(w * scale), round(GROUP_HEAD_H * scale)), (0, 0, 0, 0))
    c = Canvas(image, scale, BG)
    c.image_at(2, 7, provider, 20)
    c.text(32, 22, title, 15, ACCENT[provider], True)
    tx = 32 + fr.text_w(title, 15, True) + 10
    c.text(tx, 22, f"{count} account{'s' if count != 1 else ''}", 12, MUTED)
    c.text(w - 2, 22, caption, 12, FAINT, anchor="rs")
    return Tile(image, [])


def draw_empty(state, w, scale, ui):
    h = 170
    image = Image.new("RGBA", (round(w * scale), round(h * scale)), (0, 0, 0, 0))
    c = Canvas(image, scale, BG)
    c.outline(0, 0, w, h, RADIUS, LINE_STRONG)
    live = state.get("mode") == "live"
    c.text(w / 2, 58, "No accounts yet", 16, TEXT, True, anchor="ms")
    c.text(w / 2, 84, "Sign in to Claude Code or Codex as usual and the account appears here automatically, or add one now."
           if live else "No sample accounts.", 13, MUTED, anchor="ms")
    hits = []
    if live:
        widths = [fr.text_w(f"Add {t} account", 13) + 28 for _, t, _ in PROVIDERS]
        x = w / 2 - (sum(widths) + 10) / 2
        for (provider, title, _), bw in zip(PROVIDERS, widths):
            hot = ui.hover == "add:" + provider
            c.rect(x, 108, bw, 32, 8, (SURFACE_3 if hot else SURFACE_2) + (255,))
            c.outline(x, 108, bw, 32, 8, LINE_STRONG)
            c.text(x + bw / 2, 124, f"Add {title} account", 13, TEXT, anchor="mm", bg=SURFACE_3 if hot else SURFACE_2)
            hits.append(((x, 108, bw, 32), "add:" + provider, "hand"))
            x += bw + 10
    return Tile(image, hits)


@lru_cache(maxsize=4)
def backdrop(width, height, scale):
    """The window background: dark, with two soft tints at the top (violet left, orange right)."""
    w, h = round(width * scale), round(height * scale)
    small = Image.new("RGB", (max(1, w // 8), max(1, h // 8)), BG)
    tint = Image.new("L", small.size, 0)
    d = ImageDraw.Draw(tint)
    for cx, cy, rx, ry, strength, color in ((.12, -.08, 900, 420, .10, (125, 110, 240)), (.92, -.12, 800, 380, .08, (217, 119, 87))):
        tint = Image.new("L", small.size, 0)
        d = ImageDraw.Draw(tint)
        ex, ey = rx * scale / 8 * .7, ry * scale / 8 * .7
        x0, y0 = cx * small.width, cy * height * scale / 8
        d.ellipse((x0 - ex, y0 - ey, x0 + ex, y0 + ey), fill=round(255 * strength))
        tint = tint.filter(ImageFilter.GaussianBlur(max(1, ex * .35)))
        small.paste(color, (0, 0), tint)
    return small.resize((w, h), Image.Resampling.BILINEAR)


# ---------- overlays: menus, the date editor, toasts ----------
def panel(image, scale, x, y, w, h):
    """A floating surface (menus, editor) with its shadow, drawn straight onto the frame."""
    s = scale
    sh = shadow(w, h, s, 1.4)
    image.paste(sh, (round(x * s) - round(SHADOW * s), round(y * s) - round(SHADOW * s) + round(4 * s)), sh)
    c = Canvas(image, s, SURFACE_3)
    c.rect(x, y, w, h, 10, SURFACE_3 + (255,))
    c.outline(x, y, w, h, 10, LINE_STRONG)
    return c


def toggle(c, x, y, on, hot, bg):
    """A switch 40x22, like the web one."""
    if on:
        c.rect(x, y, 40, 22, 11, (blend(GOOD, (255, 255, 255), .08) if hot else GOOD) + (255,))
        c.dot(x + 29.5, y + 11, 7 if hot else 6.5, (255, 255, 255))
    else:
        c.outline(x, y, 40, 22, 11, (MUTED if hot else FAINT) + (255,), 1.5)
        c.dot(x + 10.5, y + 11, 7 if hot else 6.5, MUTED)


SETTINGS = (("autoSwap", "Auto swap", "Move to the account with the most headroom when a limit hits"),
            ("afk", "AFK mode", "Continue a session by itself after a usage limit"),
            ("nameMode", "Name mode", "Names instead of emails everywhere, for screen sharing"))


def settings_menu(image, scale, state, ui, x, y, prefs):
    w = 320
    rows = list(SETTINGS)
    taskbar = bool(state.get("taskbarAvailable"))
    if taskbar:
        rows.append(("taskbar", "Taskbar view", "The accounts in use, right on the taskbar"))
    displays = state.get("taskbarDisplays") or []
    chooser = taskbar and state.get("taskbar") and len(displays) > 1
    wrapped = [wrap(desc, 12, w - 80) for _, _, desc in rows]
    h = 16 + sum(30 + 16 * len(lines) for lines in wrapped) + (13 if taskbar else 0) + (34 if chooser else 0)
    c = panel(image, scale, x, y, w, h)
    hits = []
    ry = y + 8
    for (key, title, _), lines in zip(rows, wrapped):
        if key == "taskbar":
            c.line(x + 14, ry + 2, w - 28, LINE)
            ry += 13
        row_h = 30 + 16 * len(lines)
        on = prefs.get(key, bool(state.get(key)))
        locked = state.get("busy") and key in ("autoSwap", "afk")
        hot = ui.hover == "set:" + key and not locked
        toggle(c, x + 14, ry + 8, on, hot, SURFACE_3)
        c.text(x + 66, ry + 22, title, 14, TEXT if not locked else MUTED, True)
        for i, line in enumerate(lines):
            c.text(x + 66, ry + 39 + 16 * i, line, 12, MUTED)
        if not locked:
            hits.append(((x + 8, ry + 2, w - 16, row_h - 4), "set:" + key, "hand"))
        ry += row_h
    if chooser:
        c.text(x + 66, ry + 13, "Show on", 13, MUTED, anchor="lm")
        cx = x + 66 + fr.text_w("Show on", 13) + 10
        current = state.get("taskbarDisplay") or "main"
        for d in displays:
            label = d["label"].replace(" display", "")
            bw = fr.text_w(label, 12) + 16
            chosen = d["id"] == current
            hot = ui.hover == "display:" + d["id"]
            fill = (76, 195, 138, 36) if chosen else ((255, 255, 255, 16) if hot else (255, 255, 255, 0))
            if fill[3]:
                c.rect(cx, ry + 1, bw, 24, 7, fill)
            c.text(cx + bw / 2, ry + 13, label, 12, GOOD if chosen else (TEXT if hot else MUTED), anchor="mm",
                   bg=over(SURFACE_3, fill) if fill[3] else SURFACE_3)
            hits.append(((cx, ry + 1, bw, 24), "display:" + d["id"], "hand"))
            cx += bw + 4
    return (x, y, w, h), hits


def add_menu(image, scale, ui, x, y):
    w, h = 260, 12 + 2 * 36
    c = panel(image, scale, x, y, w, h)
    hits = []
    for i, (provider, title, _) in enumerate(PROVIDERS):
        ry = y + 6 + i * 36
        hot = ui.hover == "add:" + provider
        if hot:
            c.rect(x + 6, ry, w - 12, 36, 6, (255, 255, 255, 15))
        c.image_at(x + 16, ry + 10, provider, 16)
        c.text(x + 42, ry + 18, f"{title} account", 13, TEXT, anchor="lm", bg=over(SURFACE_3, (255, 255, 255, 15)) if hot else SURFACE_3)
        hits.append(((x + 6, ry, w - 12, 36), "add:" + provider, "hand"))
    return (x, y, w, h), hits


def month_grid(year, month):
    """Weeks (Monday first) of day numbers, 0 for blanks."""
    import calendar
    return calendar.Calendar(0).monthdayscalendar(year, month)


def date_editor(image, scale, ui, x, y):
    """Renews / Ends, a month calendar, where the date came from, and the buttons."""
    ed = ui.editor
    weeks = month_grid(ed["year"], ed["month"])
    w = 280
    h = 12 + 28 + 10 + 30 + 22 + len(weeks) * 30 + 8 + 34 + 12 + 32 + 12
    c = panel(image, scale, x, y, w, h)
    hits = []
    hover = ui.hover
    ry = y + 12
    kx = x + 14
    for kind, label in (("renews", "Renews"), ("ends", "Ends (cancelled)")):
        on = ed["ends"] == (kind == "ends")
        hot = hover == "ed-kind:" + kind
        c.dot(kx + 8, ry + 14, 8, (GOOD if on else (MUTED if hot else FAINT)) + (255,))
        c.dot(kx + 8, ry + 14, 6.5 if not on else 3.5, SURFACE_3 + (255,) if not on else (255, 255, 255))
        c.text(kx + 22, ry + 14, label, 13, TEXT, anchor="lm")
        bw = 22 + fr.text_w(label, 13) + 6
        hits.append(((kx - 4, ry, bw + 8, 28), "ed-kind:" + kind, "hand"))
        kx += bw + 18
    ry += 28 + 10
    # Month header
    title = time.strftime("%B %Y", (ed["year"], ed["month"], 1, 0, 0, 0, 0, 1, -1))
    c.text(x + w / 2, ry + 15, title, 13, TEXT, True, anchor="mm")
    for kind, bx in (("left", x + 14), ("right", x + w - 14 - 28)):
        hot = hover == "ed-" + kind
        if hot:
            c.rect(bx, ry + 1, 28, 28, 7, (255, 255, 255, 16))
        c.glyph(kind, bx + 14, ry + 15, 16, TEXT if hot else MUTED)
        hits.append(((bx, ry + 1, 28, 28), "ed-" + kind, "hand"))
    ry += 30
    cw = (w - 28) / 7
    for i, name in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
        c.text(x + 14 + cw * i + cw / 2, ry + 11, name, 11, FAINT, anchor="mm")
    ry += 22
    today = time.localtime()[:3]
    for week in weeks:
        for i, day in enumerate(week):
            if not day:
                continue
            cx = x + 14 + cw * i
            chosen = (ed["year"], ed["month"], day) == ed["date"]
            hot = hover == f"ed-day:{day}"
            if chosen:
                c.rect(cx + 3, ry + 1, cw - 6, 28, 7, GOOD + (255,))
            elif hot:
                c.rect(cx + 3, ry + 1, cw - 6, 28, 7, (255, 255, 255, 16))
            is_today = (ed["year"], ed["month"], day) == today
            color = ON_ACCENT if chosen else (TEXT if hot or is_today else MUTED)
            c.text(cx + cw / 2, ry + 15, str(day), 12.5, color, is_today or chosen, anchor="mm",
                   bg=GOOD if chosen else (over(SURFACE_3, (255, 255, 255, 16)) if hot else SURFACE_3))
            hits.append(((cx + 3, ry + 1, cw - 6, 28), f"ed-day:{day}", "hand"))
        ry += 30
    ry += 8
    note = {"auto": "Detected from your account. Change it if it is wrong.", "manual": "Set by you."}.get(
        ed.get("source"), "Not reported by the provider. Enter it from your billing page.")
    for i, line in enumerate(wrap(note, 11.5, w - 28)[:2]):
        c.text(x + 14, ry + 12 + i * 16, line, 11.5, FAINT)
    ry += 34 + 12
    bx = x + w - 14
    for action, label, primary in (("ed-save", "Save", True), ("ed-cancel", "Cancel", False)) + \
            ((("ed-clear", "Use detected", False),) if ed.get("source") == "manual" else ()):
        bw = fr.text_w(label, 13) + 28
        bx -= bw
        hot = hover == action
        if primary:
            fill = (TEXT if not hot else blend(TEXT, BG, .08))
            c.rect(bx, ry, bw, 32, 8, fill + (255,))
            c.text(bx + bw / 2, ry + 16, label, 13, BG, True, anchor="mm", bg=fill)
        else:
            if hot:
                c.rect(bx, ry, bw, 32, 8, (255, 255, 255, 16))
            c.text(bx + bw / 2, ry + 16, label, 13, TEXT if hot else MUTED, anchor="mm",
                   bg=over(SURFACE_3, (255, 255, 255, 16)) if hot else SURFACE_3)
        hits.append(((bx, ry, bw, 32), action, "hand"))
        bx -= 6
    return (x, y, w, h), hits


def wrap(text, size, width, bold=False):
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if fr.text_w(trial, size, bold) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


TOAST_COLORS = {"error": BAD, "ok": GOOD, "": ACCENT["codex"]}


def toasts(image, scale, items, vw, vh):
    y = vh - 20
    for text, kind, _ in reversed(items[-4:]):
        lines = wrap(text, 13, 380 - 42)
        h = 22 + 18 * len(lines)
        w = min(380, 42 + max(fr.text_w(line, 13) for line in lines) + 14)
        y -= h
        x = vw - 20 - w
        c = panel(image, scale, x, y, w, h)
        if kind == "error":
            c.outline(x, y, w, h, 11, BAD + (128,))
        c.dot(x + 18, y + 17, 4, TOAST_COLORS.get(kind, ACCENT["codex"]))
        for i, line in enumerate(lines):
            c.text(x + 32, y + 16 + i * 18, line, 13, TEXT, anchor="lm")
        y -= 8
