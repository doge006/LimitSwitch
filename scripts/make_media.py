"""Screenshots and a GIF for the README and the website, drawn by the app's own renderers with the
demo accounts (no real logins, no network). Run it on Windows so the text is Segoe UI, as users see
it (the Media workflow does):

    python scripts/make_media.py            writes docs/media/*.png and docs/media/demo.gif

  fullview.png   the full view
  panel.png      the tray panel
  taskbar.png    the taskbar view (both providers)
  demo.gif       the full view opening, a hover, and switching accounts
  icon.png       the app icon (the site's logo and favicon)
"""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

from account_switcher import flyout_render as fr, fullview  # noqa: E402
from account_switcher.web import Controller  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "media"
SCALE = 2.0            # crisp on high-density screens; the site shows them at half size
GIF_SCALE = 1.0
FPS = 25


class Clock:
    """Stands in for time.perf_counter, so animations advance frame by frame."""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Host:
    def __init__(self):
        self.timers = {}

    def invalidate(self):
        pass

    def set_timer(self, name, ms):
        self.timers[name] = ms

    def kill_timer(self, name):
        self.timers.pop(name, None)

    def has_timer(self, name):
        return name in self.timers

    def set_cursor(self, kind):
        pass

    def clipboard(self):
        return ""


def settled(controller):
    for _ in range(200):
        state = controller.snapshot()
        if not state["busy"]:
            return state
        time.sleep(0.02)
    return controller.snapshot()


def wallpaper(size, scale):
    """A soft dark desktop to set the panel and taskbar on."""
    w, h = size
    image = Image.new("RGB", (1, 2))
    image.putpixel((0, 0), (44, 52, 78))
    image.putpixel((0, 1), (18, 20, 30))
    image = image.resize((w, h), Image.Resampling.BICUBIC)
    glow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(glow).ellipse((w * .55, -h * .3, w * 1.3, h * .7), fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(120 * scale))
    image.paste((217, 119, 87), mask=glow)
    return image


def full_view_frames(controller, width, height, scale, clock):
    """Frames of the full view: it opens (tiles rise in, bars fill), a card is hovered, and an
    account is switched to."""
    state = settled(controller)
    view = fullview.FullView(controller, Host(), state)
    view.resize(width, height, scale)
    frames = []

    def run(seconds):
        for _ in range(max(1, round(seconds * FPS))):
            clock.now += 1 / FPS
            view.set_state(controller.snapshot())
            image = view.frame()
            frames.append(image.convert("RGB"))

    run(1.3)
    # Hover the second Claude account, then click it.
    target = next(a for a in state["accounts"] if a["provider"] == "claude" and not a["active"])
    card = next(item for item in view.items if item[0] == "card" and item[6]["id"] == target["id"])
    _, _, x, y, w, h, _ = card
    view.mouse_move(x + w / 2, y + h / 2 - view.scroll)
    run(0.9)
    controller.action("swap", {"id": target["id"]})
    run(0.3)
    settled(controller)
    run(1.6)
    view.mouse_leave()
    run(1.2)
    return frames, view


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    clock = Clock()
    fullview.time.perf_counter = clock  # the animations' clock
    controller = Controller()
    try:
        # The full view at rest.
        view = fullview.FullView(controller, Host(), settled(controller))
        view.resize(1100, 720, SCALE)
        view.motion.settle()
        clock.now += 5
        view.frame()
        view.motion.settle()
        image = view.frame()
        image.convert("RGB").save(OUT / "fullview.png", optimize=True)

        # The panel, over a desktop.
        state = settled(controller)
        panel, _ = fr.render(state, scale=SCALE)
        backdrop = wallpaper((panel.width + round(80 * SCALE), panel.height + round(80 * SCALE)), SCALE).convert("RGBA")
        backdrop.alpha_composite(panel, (round(40 * SCALE), round(40 * SCALE)))
        backdrop.convert("RGB").save(OUT / "panel.png", optimize=True)

        # The taskbar view: both providers' blocks on a dark taskbar strip.
        blocks = [fr.render_block(state, provider, scale=SCALE)[0] for provider in ("claude", "codex")]
        gap, pad = round(8 * SCALE), round(12 * SCALE)
        strip_h = round(48 * SCALE)
        strip_w = sum(b.width for b in blocks) + gap * (len(blocks) - 1) + 2 * pad
        strip = Image.new("RGBA", (strip_w, strip_h), (28, 30, 38, 255))
        x = pad
        for block in blocks:
            strip.alpha_composite(block, (x, (strip_h - block.height) // 2))
            x += block.width + gap
        strip.convert("RGB").save(OUT / "taskbar.png", optimize=True)

        # The GIF, at 1x so it stays small.
        controller.close()
        controller = Controller()
        frames, _ = full_view_frames(controller, 1000, 640, GIF_SCALE, clock)
        palette = frames[len(frames) // 2].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        gif = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
        gif[0].save(OUT / "demo.gif", save_all=True, append_images=gif[1:], duration=round(1000 / FPS), loop=0,
                    optimize=True, disposal=1)
    finally:
        controller.close()
    icon = Path(fr.__file__).with_name("static") / "assets" / "switcher.png"
    with Image.open(icon) as source:
        source.resize((128, 128), Image.Resampling.LANCZOS).save(OUT / "icon.png", optimize=True)
    for path in sorted(OUT.iterdir()):
        print(f"{path.name}: {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
