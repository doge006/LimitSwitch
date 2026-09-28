"""Render the full view (Pillow, as Windows draws it) through a fixed script of states and print a
hash per frame, to check that a refactor leaves every pixel the same (a development tool):

    python scripts/render_frames.py > after.txt   # and the same on the old code, then diff
"""
import hashlib
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ACCOUNT_SWITCHER_HOME", "/tmp/limitswitcher-render-frames")

from account_switcher import fullview  # noqa: E402
from account_switcher.web import Controller  # noqa: E402


class Host:
    def __init__(self):
        self.timers = {}

    def invalidate(self): pass
    def set_timer(self, name, ms): self.timers[name] = ms
    def kill_timer(self, name): self.timers.pop(name, None)
    def has_timer(self, name): return name in self.timers
    def set_cursor(self, kind): pass
    def clipboard(self): return ""


def main():
    import random
    random.seed(7)
    clock = [1000.0]
    frames = []
    with mock.patch.object(fullview.time, "perf_counter", lambda: clock[0]), \
            mock.patch.object(fullview.time, "monotonic", lambda: clock[0]), \
            mock.patch("time.time", lambda: 1_790_000_000.0):
        controller = Controller()
        try:
                for scale in (1.0, 1.25, 1.5, 2.0):
                    view = fullview.FullView(controller, Host(), controller.snapshot())
                    view.resize(900, 640, scale)

                    def step(n, label):
                        for i in range(n):
                            clock[0] += 0.04
                            view.set_state(controller.snapshot())
                            image = view.frame()
                            frames.append(f"{scale} {label} {i} {hashlib.sha256(image.tobytes()).hexdigest()[:16]}")

                    step(20, "rise")
                    cards = [item for item in view.items if item[0] == "card"]
                    for _, _, x, y, w, h, _ in cards[:3]:
                        view.mouse_move(x + w / 2, y + h / 2 - view.scroll)
                        step(6, "hover")
                        view.mouse_move(x + w * 0.85, y + h - 40 - view.scroll)
                        step(5, "button")
                    view.activate("settings")
                    step(8, "settings")
                    view.activate("settings")
                    view.activate("add")
                    step(8, "add")
                    view.activate("add")
                    view.toast("Swapped to another account", "ok")
                    view.toast("Something went wrong while checking", "error")
                    step(10, "toasts")
                    view.wheel(200)
                    step(4, "scroll")
        finally:
            controller.close()
    print("\n".join(frames))


if __name__ == "__main__":
    main()
