"""Draws the full view with macOS itself (AppKit and Core Graphics), straight into the window.

fullview_render records what each card, the header and the group headings show as drawing calls
(Recorder); menus, the date editor and toasts draw through a Surface; bars and percentages through
a canvas. This module carries those calls out with the system's own drawing, every frame, in
drawRect: no picture of a card or of the window is kept, so the full view's memory is its code and
the window's own buffer. It follows fullview_render.Canvas exactly where pixels land (device px
rounded the same way), so both look the same; text is the system font drawn by Core Text.

Only shadows are images: one small blurred card per strength (fullview_render.shadow, a few
hundred KB), cut into nine pieces and stretched to each card's size.
"""
from functools import lru_cache
import math
from pathlib import Path

from AppKit import (NSAffineTransform, NSAttributedString, NSBezierPath, NSColor, NSCompositingOperationSourceOver,
                    NSEvenOddWindingRule, NSFont, NSFontAttributeName, NSFontWeightRegular, NSFontWeightSemibold,
                    NSForegroundColorAttributeName, NSGraphicsContext, NSImage, NSImageInterpolationHigh,
                    NSLineCapStyleButt, NSLineJoinStyleRound, NSZeroRect)
from Foundation import NSData, NSMakeRect
import Quartz

from . import fullview_render as vr

ASSETS = Path(__file__).resolve().parent / "static" / "assets"
SHADOW_CORE = 56  # logical px: a shadow's corner piece reaches this far in (radius + 3 blur sigmas + offset)


# ---------- colours, fonts, text ----------
@lru_cache(maxsize=512)
def color(rgba):
    alpha = rgba[3] / 255 if len(rgba) == 4 else 1.0
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgba[0] / 255, rgba[1] / 255, rgba[2] / 255, alpha)


@lru_cache(maxsize=64)
def font(size, bold):
    return NSFont.systemFontOfSize_weight_(size, NSFontWeightSemibold if bold else NSFontWeightRegular)


@lru_cache(maxsize=128)
def label(value, size, bold, rgb):
    return NSAttributedString.alloc().initWithString_attributes_(
        value, {NSFontAttributeName: font(size, bold), NSForegroundColorAttributeName: color(rgb)})


@lru_cache(maxsize=1024)
def text_w(value, size, bold=False):
    """Logical width of a label as it is drawn here (the layout's measure on macOS)."""
    return float(NSAttributedString.alloc().initWithString_attributes_(
        value, {NSFontAttributeName: font(size, bool(bold))}).size().width)


@lru_cache(maxsize=8)
def asset(name):
    return NSImage.alloc().initWithContentsOfFile_(str(ASSETS / f"{name}.png"))


def cg_image(raw, width, height):
    """A CGImage over premultiplied RGBA bytes."""
    data = NSData.dataWithBytes_length_(raw, len(raw))
    return Quartz.CGImageCreate(width, height, 8, 32, width * 4, Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB),
                                Quartz.kCGImageAlphaPremultipliedLast, Quartz.CGDataProviderCreateWithCFData(data),
                                None, False, Quartz.kCGRenderingIntentDefault)


# ---------- shadows: nine pieces of one small blurred card ----------
@lru_cache(maxsize=8)
def shadow_pieces(strength, scale):
    """(pieces, source size, corner size): fullview_render.shadow of a small card, cut in nine."""
    image = vr.shadow(2 * SHADOW_CORE, 2 * SHADOW_CORE, scale, strength)
    size = image.width
    cut = round(SHADOW_CORE * scale) + round(vr.SHADOW * scale) - 1
    whole = cg_image(image.convert("RGBa").tobytes(), size, image.height)
    edges = (0, cut, size - cut, size)
    pieces = {}
    for i in range(3):
        for j in range(3):
            pieces[i, j] = Quartz.CGImageCreateWithImageInRect(
                whole, Quartz.CGRectMake(edges[i], edges[j], edges[i + 1] - edges[i], edges[j + 1] - edges[j]))
    return pieces, size, cut


@lru_cache(maxsize=8)
def shadow_whole(w, h, strength, scale):
    """A small overlay's shadow (a toast), too small for nine pieces: drawn whole."""
    image = vr.shadow(w, h, scale, strength)
    return cg_image(image.convert("RGBa").tobytes(), image.width, image.height)


# ---------- the painter ----------
class Painter:
    """One frame's drawing into the view's current graphics context (a flipped NSView)."""

    def __init__(self, view, scale):
        self.view, self.s = view, scale
        self.ctx = NSGraphicsContext.currentContext().CGContext()
        NSGraphicsContext.currentContext().setImageInterpolation_(NSImageInterpolationHigh)

    def points(self, x0, y0, w, h):
        """A device-px box as a rect in the view's points."""
        s = self.s
        return NSMakeRect(x0 / s, y0 / s, w / s, h / s)

    def background(self):
        color(vr.BG).set()
        NSBezierPath.fillRect_(self.view.bounds())

    def visible(self, box):
        return self.view.needsToDrawRect_(self.points(box[0], box[1], box[2] - box[0], box[3] - box[1]))

    def canvas(self, bg, ox=0, oy=0):
        return Canvas(self, bg, ox, oy)

    def surface(self):
        return Surface(self)

    def image(self, picture, x0, y0, w, h, alpha=1.0):
        """Draw a CGImage over the device-px box (the view is flipped; CG draws images bottom-up)."""
        rect = self.points(x0, y0, w, h)
        ctx = self.ctx
        Quartz.CGContextSaveGState(ctx)
        Quartz.CGContextSetInterpolationQuality(ctx, Quartz.kCGInterpolationNone)
        Quartz.CGContextTranslateCTM(ctx, 0, rect.origin.y + rect.size.height)
        Quartz.CGContextScaleCTM(ctx, 1, -1)
        if alpha < 1:
            Quartz.CGContextSetAlpha(ctx, alpha)
        Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(rect.origin.x, 0, rect.size.width, rect.size.height), picture)
        Quartz.CGContextRestoreGState(ctx)

    def shadow(self, x0, y0, w, h, logical_w, logical_h, strength):
        """fullview_render.shadow(logical_w, logical_h) with its top left at device (x0, y0),
        w x h device px."""
        pieces, size, cut = shadow_pieces(strength, self.s)
        if w < 2 * cut + 2 or h < 2 * cut + 2:
            self.image(shadow_whole(logical_w, logical_h, strength, self.s), x0, y0, w, h)
            return
        xs, ys = (0, cut, w - cut, w), (0, cut, h - cut, h)
        for i in range(3):
            for j in range(3):
                self.image(pieces[i, j], x0 + xs[i], y0 + ys[j], xs[i + 1] - xs[i], ys[j + 1] - ys[j])

    def group_begin(self, alpha, box=None, layer=False):
        """What is drawn until group_end shows at `alpha`. Fades (a card rising in, a menu or
        toast) just draw each part at that opacity: no offscreen buffer, which at Retina size
        is several MB a card. layer=True flattens first (a dimmed card, where parts overlap)."""
        Quartz.CGContextSaveGState(self.ctx)
        Quartz.CGContextSetAlpha(self.ctx, max(0.0, min(1.0, alpha)))
        self.layers = getattr(self, "layers", [])
        self.layers.append(layer)
        if layer:
            Quartz.CGContextBeginTransparencyLayerWithRect(self.ctx, self.points(*box), None)

    def group_end(self):
        if self.layers.pop():
            Quartz.CGContextEndTransparencyLayer(self.ctx)
        Quartz.CGContextRestoreGState(self.ctx)

    def tile(self, tile, tx, ty, rise):
        """A recorded tile with its top left at device (tx, ty); fading in while it rises."""
        faded = rise < 1
        if faded:
            self.group_begin(rise, (tx, ty, tile.width, tile.height))
        if tile.shape and tile.shape[0] == "card":
            self.card(tile, tx, ty)
        else:
            replay(self.canvas(vr.BG, tx, ty), tile.ops)
        if faded:
            self.group_end()

    def card(self, tile, tx, ty):
        """A card: its shadow, then the rounded body with what it shows and its border (dimmed
        to .72 when the account is spent), as fullview_render.draw_card composes it."""
        _, w, h, border, spent = tile.shape
        s, m = self.s, tile.margin
        self.shadow(tx, ty, tile.width, tile.height, w, h, 1.0)
        bw, bh = round(w * s), round(h * s)
        if spent:
            self.group_begin(184 / 255, (tx + m, ty + m, bw, bh), layer=True)
        NSGraphicsContext.saveGraphicsState()
        radius = round(vr.RADIUS * s) / s
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.points(tx + m, ty + m, bw, bh), radius, radius).addClip()
        color(vr.SURFACE).set()
        NSBezierPath.fillRect_(self.points(tx + m, ty + m, bw, bh))
        c = self.canvas(vr.SURFACE, tx + m, ty + m)
        replay(c, tile.ops)
        c.outline(0, 0, w, h, vr.RADIUS, border, 1)
        NSGraphicsContext.restoreGraphicsState()
        if spent:
            self.group_end()


class Surface:
    """fullview_render.Surface for the overlays, drawn straight into the window."""

    def __init__(self, painter):
        self.p = painter

    def canvas(self, bg):
        return self.p.canvas(bg)

    def shadow(self, x, y, w, h, strength):
        s, m = self.p.s, round(vr.SHADOW * self.p.s)
        self.p.shadow(round(x * s) - m, round(y * s) - m + round(4 * s), round(w * s) + 2 * m, round(h * s) + 2 * m,
                      w, h, strength)

    def fade_begin(self, t):
        self.p.group_begin(t)
        return True

    def fade_end(self, before, box, t):
        self.p.group_end()


class Canvas:
    """fullview_render.Canvas with the platform's drawing: the same logical-px calls, landing on
    the same device pixels. (ox, oy): device px of the canvas's 0, 0 in the view."""

    def __init__(self, painter, bg, ox, oy):
        self.p, self.s, self.bg, self.ox, self.oy = painter, painter.s, bg, ox, oy

    def px(self, v):
        return round(v * self.s)

    def box(self, x, y, w, h):
        x0, y0 = self.px(x), self.px(y)
        return x0 + self.ox, y0 + self.oy, max(1, self.px(x + w) - x0), max(1, self.px(y + h) - y0)

    def rect(self, x, y, w, h, r, fill):
        x0, y0, pw, ph = self.box(x, y, w, h)
        radius = min(self.px(r), pw / 2, ph / 2) / self.s
        color(tuple(fill)).set()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.p.points(x0, y0, pw, ph), radius, radius).fill()

    def outline(self, x, y, w, h, r, color_, width=1):
        x0, y0, pw, ph = self.box(x, y, w, h)
        t = max(1, round(width * self.s))
        rp = min(self.px(r), pw / 2, ph / 2)
        path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.p.points(x0, y0, pw, ph), rp / self.s, rp / self.s)
        if pw > 2 * t and ph > 2 * t:
            inner = max(0, rp - t) / self.s
            path.appendBezierPath_(NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                self.p.points(x0 + t, y0 + t, pw - 2 * t, ph - 2 * t), inner, inner))
            path.setWindingRule_(NSEvenOddWindingRule)
        color(tuple(color_)).set()
        path.fill()

    def dot(self, cx, cy, r, fill):
        self.rect(cx - r, cy - r, 2 * r, 2 * r, r, fill)

    def line(self, x, y, w, color_):
        x0, y0 = self.px(x), self.px(y)
        x1 = self.px(x + w)
        color(vr.over(self.bg, color_) if len(color_) == 4 else tuple(color_)).set()
        NSBezierPath.fillRect_(self.p.points(x0 + self.ox, y0 + self.oy, x1 - x0, max(1, round(self.s))))

    def text(self, x, y, value, size, fill, bold=False, anchor="ls", bg=None):
        if not value:
            return
        if len(fill) == 4:
            fill = vr.over(bg or self.bg, fill)
        f = font(size, bool(bold))
        string = label(value, size, bool(bold), tuple(fill[:3]))
        width = string.size().width
        left = x - (width / 2 if anchor[0] == "m" else width if anchor[0] == "r" else 0)
        baseline = y + ((f.ascender() + f.descender()) / 2 if anchor[1] == "m" else 0)
        # Without the line-fragment option the rect's origin is the first line's baseline.
        string.drawWithRect_options_context_(NSMakeRect(left + self.ox / self.s, baseline + self.oy / self.s, width + 4, 0),
                                             0, None)

    def image_at(self, x, y, name, size):
        picture = asset(name)
        if picture is not None:
            side = self.px(size)
            picture.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                self.p.points(self.px(x) + self.ox, self.px(y) + self.oy, side, side), NSZeroRect,
                NSCompositingOperationSourceOver, 1.0, True, None)

    def mark(self, x, y, size, radius):
        side = self.px(size)
        rect = self.p.points(self.px(x) + self.ox, self.px(y) + self.oy, side, side)
        NSGraphicsContext.saveGraphicsState()
        r = self.px(radius) / self.s
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, r, r).addClip()
        picture = asset("switcher")
        if picture is not None:
            picture.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                rect, NSZeroRect, NSCompositingOperationSourceOver, 1.0, True, None)
        NSGraphicsContext.restoreGraphicsState()

    def glyph(self, kind, cx, cy, size, color_, degrees=0):
        """fullview_render.glyph's line icons, stroked as paths (in a box of `size`, centred)."""
        side = self.px(size)
        left, top = self.px(cx) - side // 2 + self.ox, self.px(cy) - side // 2 + self.oy
        s = self.s

        def pt(u, v):
            return ((left + u * side) / s, (top + v * side) / s)

        width = max(4, round(side * 4 * 0.1)) / 4 / s  # as drawn at 4x and reduced
        path = NSBezierPath.bezierPath()
        path.setLineWidth_(width)
        path.setLineJoinStyle_(NSLineJoinStyleRound)
        path.setLineCapStyle_(NSLineCapStyleButt)

        def polyline(*points):
            path.moveToPoint_(pt(*points[0]))
            for p in points[1:]:
                path.lineToPoint_(pt(*p))

        if kind == "caret":
            polyline((.25, .38), (.5, .63), (.75, .38))
        elif kind == "plus":
            polyline((.5, .2), (.5, .8))
            polyline((.2, .5), (.8, .5))
        elif kind == "refresh":
            # Pillow's arc: 40 to 330 degrees, clockwise from 3 o'clock, the band inside its box
            r = .32 - width * s / side / 2
            polyline(*[(.5 + r * math.cos(math.radians(a)), .5 + r * math.sin(math.radians(a))) for a in range(40, 331, 5)])
            polyline((.82, .12), (.82, .42), (.52, .42))
        elif kind == "check":
            polyline((.2, .52), (.42, .72), (.8, .3))
        elif kind == "left":
            polyline((.6, .25), (.35, .5), (.6, .75))
        elif kind == "right":
            polyline((.4, .25), (.65, .5), (.4, .75))
        NSGraphicsContext.saveGraphicsState()
        if degrees:  # anticlockwise on screen (the view is flipped: y grows down)
            cxp, cyp = pt(.5, .5)
            turn = NSAffineTransform.transform()
            turn.translateXBy_yBy_(cxp, cyp)
            turn.rotateByDegrees_(-degrees)
            turn.translateXBy_yBy_(-cxp, -cyp)
            turn.concat()
        color(tuple(color_)).set()
        path.stroke()
        NSGraphicsContext.restoreGraphicsState()


def replay(canvas, ops):
    """Carry out a Recorder's calls on `canvas`."""
    for op in ops:
        kind = op[0]
        if kind == "rect":
            canvas.rect(*op[1:])
        elif kind == "outline":
            canvas.outline(*op[1:])
        elif kind == "line":
            canvas.line(*op[1:])
        elif kind == "text":
            canvas.text(*op[1:])
        elif kind == "image":
            canvas.image_at(*op[1:])
        elif kind == "glyph":
            canvas.glyph(*op[1:])
        elif kind == "mark":
            canvas.mark(*op[1:])
