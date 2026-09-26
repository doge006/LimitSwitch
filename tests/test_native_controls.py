import unittest
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

from account_switcher.native_controls import Motion, FluentButton


class ClockWidget:
    def __init__(self):
        self.clock = 0.
        self.jobs = {}
        self.serial = 0

    def bind(self, *args, **kwargs):
        pass

    def after(self, milliseconds, callback):
        self.serial += 1
        self.jobs[self.serial] = (self.clock + milliseconds / 1000, callback)
        return self.serial

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, seconds):
        deadline = self.clock + seconds
        while self.jobs:
            key = min(self.jobs, key=lambda key: self.jobs[key][0])
            at, callback = self.jobs[key]
            if at > deadline:
                break
            self.jobs.pop(key)
            self.clock = at
            callback()
        self.clock = deadline


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.widget = ClockWidget()
        self.addCleanup(patch.stopall)
        patch("account_switcher.native_controls.animations_enabled", return_value=True).start()
        patch("account_switcher.native_controls.time.monotonic", side_effect=lambda: self.widget.clock).start()

    def test_retarget_preserves_value_and_only_completes_latest_target(self):
        completions = []
        rendered = []
        motion = Motion(self.widget, 0, rendered.append)
        motion.to(1, done=lambda: completions.append("stale"))
        self.widget.advance(.064)
        current = motion.value
        self.assertGreater(current, 0)
        self.assertLess(current, 1)
        motion.to(0, done=lambda: completions.append("latest"))
        self.assertEqual(motion.value, current)
        self.widget.advance(.3)
        self.assertEqual(motion.value, 0)
        self.assertEqual(completions, ["latest"])
        self.assertIsNone(motion.job)
        self.assertEqual(self.widget.jobs, {})

    def test_reduced_motion_and_keyboard_complete_synchronously(self):
        motion = Motion(self.widget, 0, lambda value: None)
        motion.to(1, animate=False)
        self.assertEqual(motion.value, 1)
        with patch("account_switcher.native_controls.animations_enabled", return_value=False):
            motion.to(0)
        self.assertEqual(motion.value, 0)
        self.assertEqual(self.widget.jobs, {})

    def test_destroy_cancels_callbacks(self):
        motion = Motion(self.widget, 0, lambda value: None)
        motion.to(1, done=lambda: self.fail("Destroyed animation completed"))
        motion._destroyed(SimpleNamespace(widget=self.widget))
        self.widget.advance(1)
        self.assertEqual(self.widget.jobs, {})


@unittest.skipUnless(sys.platform == 'win32' or os.environ.get('DISPLAY'), 'GUI display required')
class FluentButtonTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.calls = []
        self.button = FluentButton(self.root, text="Swap", command=lambda: self.calls.append("swap"))
        self.button.pack()
        self.root.update_idletasks()

    def test_release_outside_and_disabled_do_not_invoke(self):
        event = SimpleNamespace(x=0, y=0)
        self.button._down(event)
        self.assertEqual(self.calls, [])
        self.button._up(SimpleNamespace(x=-1, y=0))
        self.assertEqual(self.calls, [])
        self.button._down(event)
        self.button._up(event)
        self.assertEqual(self.calls, ["swap"])
        self.button.configure(state="disabled", text="Active")
        self.button._keyboard(event)
        self.button._down(event)
        self.button._up(event)
        self.assertEqual(self.calls, ["swap"])

    def test_keyboard_and_destroy_leave_no_motion_jobs(self):
        self.button._enter(None)
        self.button._keyboard(None)
        self.assertEqual(self.calls, ["swap"])
        self.assertIsNone(self.button.hover_motion.job)
        self.assertIsNone(self.button.press_motion.job)
        self.button._enter(None)
        self.button.destroy()
        self.assertIsNone(self.button.hover_motion.job)
        self.assertIsNone(self.button.press_motion.job)


if __name__ == "__main__":
    unittest.main()
