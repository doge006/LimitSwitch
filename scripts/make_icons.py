"""Draws the app icon (a development tool; its output is committed under static/assets).

    python scripts/make_icons.py

The mark: two arrows chasing each other around a circle, Claude's orange and Codex's violet,
on a dark tile. Written as
  switcher.png      256 px, the tile filling the image (in-app header, tray, web favicon)
  switcher.ico      Windows sizes, 16-256 px
  appicon-mac.png   1024 px on the macOS icon grid (824 px squircle, soft shadow), for the Dock
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ASSETS = Path(__file__).resolve().parent.parent / "account_switcher" / "static" / "assets"
SS = 4  # supersampling
TOP, BOTTOM = (54, 60, 76), (16, 18, 24)
ORANGE = ((236, 146, 112), (217, 119, 87))     # Claude (#D97757)
VIOLET = ((120, 150, 255), (150, 110, 250))    # Codex (blue to violet)


def squircle(size, exponent=5.0):
    """A superellipse mask (the macOS icon shape), size x size."""
    mask = Image.new("L", (size, size), 0)
    half = size / 2
    points = []
    for i in range(720):
        t = 2 * math.pi * i / 720
        c, s = math.cos(t), math.sin(t)
        x = half + half * math.copysign(abs(c) ** (2 / exponent), c)
        y = half + half * math.copysign(abs(s) ** (2 / exponent), s)
        points.append((x, y))
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask


def gradient(size, top, bottom):
    column = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        column.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return column.resize((size, size))


def arrow(size, start, end, colors, radius, width):
    """One arc from start to end degrees (clockwise on screen) with an arrowhead at the end,
    filled with a diagonal gradient. Returns an RGBA layer."""
    c = size / 2
    shape = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(shape)
    box = (c - radius, c - radius, c + radius, c + radius)
    d.arc(box, start, end, fill=255, width=round(width))
    a = math.radians(start)  # round cap at the tail
    tail = (c + (radius - width / 2) * math.cos(a), c + (radius - width / 2) * math.sin(a))
    d.ellipse((tail[0] - width / 2, tail[1] - width / 2, tail[0] + width / 2, tail[1] + width / 2), fill=255)
    a = math.radians(end)
    mid = radius - width / 2
    p = (c + mid * math.cos(a), c + mid * math.sin(a))
    n = (math.cos(a), math.sin(a))          # outward
    t = (-math.sin(a), math.cos(a))         # clockwise tangent
    head, spread = width * 1.05, width * 0.98
    d.polygon([(p[0] + t[0] * head, p[1] + t[1] * head),
               (p[0] + n[0] * spread, p[1] + n[1] * spread),
               (p[0] - n[0] * spread, p[1] - n[1] * spread)], fill=255)
    fill = gradient(size, *colors).rotate(-35, resample=Image.Resampling.BICUBIC, expand=False)
    layer = fill.convert("RGBA")
    layer.putalpha(shape)
    return layer


def tile(size):
    """The dark tile with the mark, filling size x size (supersampled internally)."""
    big = size * SS
    base = gradient(big, TOP, BOTTOM).convert("RGBA")
    radius, width = big * .30, big * .125
    base.alpha_composite(arrow(big, 208, 334, ORANGE, radius, width))   # over the top, head pointing down
    base.alpha_composite(arrow(big, 28, 154, VIOLET, radius, width))    # under the bottom, head pointing up
    mask = squircle(big)
    base.putalpha(mask)
    return base.resize((size, size), Image.Resampling.LANCZOS)


def mac_icon():
    """1024 px on the macOS grid: an 824 px squircle, centred slightly high, with a soft shadow."""
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    body = tile(824)
    shadow = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 110), (100, 112), body.getchannel("A"))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(body, (100, 92))
    return canvas


if __name__ == "__main__":
    tile(256).save(ASSETS / "switcher.png")
    tile(256).save(ASSETS / "switcher.ico", sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
    mac_icon().save(ASSETS / "appicon-mac.png")
    print("wrote", ", ".join(p.name for p in (ASSETS / "switcher.png", ASSETS / "switcher.ico", ASSETS / "appicon-mac.png")))
