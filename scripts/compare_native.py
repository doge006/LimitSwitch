"""The macOS full view drawn natively (fullview_cg) next to the Pillow drawing (Windows), in demo
mode, off screen: saves both and their difference as PNGs, prints how far apart they are, and the
memory it took (a development and CI tool, macOS only).

    /Applications/LimitSwitcher.app/Contents/Resources/runtime/bin/python3 -B scripts/compare_native.py shots
"""
import os
import re
import subprocess
import sys
import time

APP = "/Applications/LimitSwitcher.app/Contents/Resources/app"
here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, here if os.path.isdir(os.path.join(here, "account_switcher")) else APP)
sys.dont_write_bytecode = True
os.environ.setdefault("ACCOUNT_SWITCHER_HOME", "/tmp/limitswitcher-compare")

import Quartz  # noqa: E402
from AppKit import NSGraphicsContext  # noqa: E402
from Foundation import NSMakeRect  # noqa: E402

from account_switcher import fullview, fullview_cg as cg, fullview_render as vr  # noqa: E402
from account_switcher.web import Controller  # noqa: E402

W, H, SCALE = 756, 570, 2.0  # half a 14" MacBook Pro screen: 1512 x 1140 device px


def footprint():
    out = subprocess.run(["footprint", "-p", str(os.getpid())], capture_output=True, text=True).stdout
    m = re.search(r"Footprint:\s+([\d.]+)\s*(KB|MB|GB)", out)
    if not m:
        return 0.0
    value = float(m.group(1))
    return value / 1024 if m.group(2) == "KB" else value * 1024 if m.group(2) == "GB" else value


class Host:
    def __init__(self):
        self.timers = {}

    def invalidate(self): pass
    def set_timer(self, name, ms): self.timers[name] = ms
    def kill_timer(self, name): self.timers.pop(name, None)
    def has_timer(self, name): return name in self.timers
    def set_cursor(self, kind): pass
    def clipboard(self): return ""


class OffscreenView:
    """What the painter asks of its NSView."""
    def bounds(self): return NSMakeRect(0, 0, W, H)
    def needsToDrawRect_(self, rect): return True


def native_frame(view, context):
    """One frame drawn natively into `context`; returns its pixels (RGBA bytes, top row first)."""
    Quartz.CGContextSaveGState(context)
    Quartz.CGContextTranslateCTM(context, 0, H * SCALE)  # flipped, like the view
    Quartz.CGContextScaleCTM(context, SCALE, -SCALE)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(NSGraphicsContext.graphicsContextWithCGContext_flipped_(context, True))
    view.draw_native(cg.Painter(OffscreenView(), SCALE))
    NSGraphicsContext.restoreGraphicsState()
    Quartz.CGContextRestoreGState(context)


def to_png(context, path):
    image = Quartz.CGBitmapContextCreateImage(context)
    url = Quartz.CFURLCreateWithFileSystemPath(None, path, Quartz.kCFURLPOSIXPathStyle, False)
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, image, None)
    Quartz.CGImageDestinationFinalize(dest)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shots"
    os.makedirs(out, exist_ok=True)
    clock = [1000.0]
    fullview.time.perf_counter = lambda: clock[0]
    fullview.time.monotonic = lambda: clock[0]
    controller = Controller()
    start = footprint()
    vr.set_measure(cg.text_w)
    native = fullview.FullView(controller, Host(), controller.snapshot())
    native.native = True
    native.resize(W, H, SCALE)
    space = Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB)
    context = Quartz.CGBitmapContextCreate(None, int(W * SCALE), int(H * SCALE), 8, 0, space,
                                           Quartz.kCGImageAlphaPremultipliedLast)
    timings = []
    for _ in range(20):  # rising in
        clock[0] += 0.04
        t = time.perf_counter()
        native_frame(native, context)
        timings.append(time.perf_counter() - t)
    cards = [item for item in native.items if item[0] == "card"]
    for _, _, x, y, w, h, _ in cards[:3]:  # hovering
        native.mouse_move(x + w / 2, y + h / 2 - native.scroll)
        for _ in range(12):
            clock[0] += 0.04
            t = time.perf_counter()
            native_frame(native, context)
            timings.append(time.perf_counter() - t)
    native.mouse_move(5, 5)
    for _ in range(12):
        clock[0] += 0.04
        native_frame(native, context)
    settled = footprint()
    to_png(context, os.path.join(out, "native.png"))
    shot = {"native": os.path.join(out, "native.png")}
    native.activate("settings")
    for _ in range(12):
        clock[0] += 0.04
        native_frame(native, context)
    to_png(context, os.path.join(out, "native-settings.png"))
    peak = footprint()
    timings.sort()
    print(f"native: {len(timings)} frames at {W}x{H} pt, scale {SCALE}: median {timings[len(timings) // 2] * 1000:.1f} ms, "
          f"slowest {timings[-1] * 1000:.1f} ms")
    print(f"memory (this process): {start:.0f} MB before, {settled:.0f} MB after hovering, {peak:.0f} MB with the menu open")

    # The same page drawn with Pillow (as on Windows), for comparison
    vr.set_measure(None)
    from PIL import Image, ImageChops
    pil = fullview.FullView(controller, Host(), controller.snapshot())
    pil.resize(W, H, SCALE)
    for _ in range(20):
        clock[0] += 0.04
        frame = pil.frame()
    for _ in range(12):
        clock[0] += 0.04
        frame = pil.frame()
    frame.save(os.path.join(out, "pillow.png"))
    theirs = Image.open(shot["native"]).convert("RGB")
    diff = ImageChops.difference(frame.convert("RGB"), theirs)
    diff.point(lambda v: min(255, v * 4)).save(os.path.join(out, "difference-x4.png"))
    histogram = diff.convert("L").histogram()
    total = sum(histogram)
    mean = sum(i * n for i, n in enumerate(histogram)) / total
    close = sum(histogram[:4]) / total
    print(f"native vs Pillow: mean difference {mean:.2f} / 255, {close * 100:.1f}% of pixels within 3 levels")
    controller.close()


if __name__ == "__main__":
    main()
