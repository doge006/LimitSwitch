import unittest

try:
    from account_switcher import flyout_render as fr
except ImportError as error:  # Pillow not installed
    raise unittest.SkipTest(f"Pillow missing: {error}")
from account_switcher.web import Controller


class FlyoutRenderTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(simulator=True)
        self.state = self.controller.snapshot()

    def tearDown(self):
        self.controller.close()

    def actions(self, state, hover=None, scale=1.0):
        image, hits = fr.render(state, hover, scale)
        return image, [action for _, action in hits]

    def test_regions_cover_every_control(self):
        image, actions = self.actions(self.state)
        self.assertEqual(image.mode, "RGBA")
        self.assertEqual(image.width, fr.WIDTH + 2 * fr.MARGIN)
        # Only accounts you can switch to are clickable; active ones are not.
        self.assertEqual(actions, ["full", "pin", "swap:claude-b", "swap:codex-b", "toggle:autoSwap", "toggle:afk", "quit"])

    def test_hit_test_finds_rows(self):
        _, hits = fr.render(self.state)
        (x, y, w, h), _ = next(item for item in hits if item[1] == "swap:claude-b")
        self.assertEqual(fr.hit_test(hits, x + w / 2, y + h / 2), "swap:claude-b")
        self.assertIsNone(fr.hit_test(hits, 1, 1))  # shadow margin is not clickable

    def test_corners_are_transparent_and_panel_is_opaque(self):
        image, _ = fr.render(self.state)
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        middle = image.getpixel((image.width // 2, image.height // 2))
        self.assertEqual(middle[3], 255)

    def test_scales_for_high_dpi(self):
        small, _ = fr.render(self.state, scale=1.0)
        large, _ = fr.render(self.state, scale=2.0)
        self.assertAlmostEqual(large.width, small.width * 2, delta=2)

    def test_busy_and_exhausted_accounts_are_not_clickable(self):
        state = self.controller.snapshot()
        state["accounts"][1]["eligible"] = False
        state["accounts"][1]["windows"][0]["used"] = 100
        _, actions = self.actions(state)
        self.assertNotIn("swap:claude-b", actions)
        state["busy"] = True
        _, actions = self.actions(state)
        self.assertEqual(actions, ["full", "pin", "quit"])

    def test_grows_with_more_accounts_without_scrolling(self):
        state = self.controller.snapshot()
        extra = dict(state["accounts"][1], id="claude-c", active=False)
        taller, _ = fr.render(dict(state, accounts=state["accounts"] + [extra]))
        normal, _ = fr.render(state)
        self.assertEqual(taller.height - normal.height, fr.ROW_H + 2)

    def test_names_are_emails_and_renewal_is_shown(self):
        layout, _ = fr.build(self.state)
        texts = [t[2] for t in layout.texts]
        self.assertIn("personal@example.com", texts)
        self.assertNotIn("Personal", texts)
        self.assertTrue(any(t.endswith("m") and t[0].isdigit() for t in texts))  # e.g. "2h 0m"
        self.assertTrue(any(op[0] == "clock" for op in layout.shapes))

    def test_pending_switch_and_status_notes(self):
        layout, _ = fr.build(self.state, pending="claude-b")
        self.assertIn("Switching…", [t[2] for t in layout.texts])
        self.assertNotIn("swap:codex-b", [a for _, a in layout.hits])  # no double switching
        state = self.controller.snapshot()
        state["accounts"][1]["status"] = "Login expired; sign in again"
        layout, _ = fr.build(state)
        self.assertIn("Sign in again", [t[2] for t in layout.texts])

    def test_empty_state_offers_adding_accounts(self):
        state = dict(self.state, accounts=[])
        _, actions = self.actions(state)
        self.assertIn("add:claude", actions)
        self.assertIn("add:codex", actions)

    def test_menu_renders_with_checks(self):
        items = [{"action": "panel", "label": "Open panel", "bold": True}, "-",
                 {"action": "toggle:afk", "label": "AFK mode", "checked": True},
                 {"action": "x", "label": "Disabled", "enabled": False}]
        image, hits = fr.render_menu(items, hover="toggle:afk", scale=1.5)
        self.assertEqual([a for _, a in hits], ["panel", "toggle:afk"])
        self.assertEqual(image.getpixel((0, 0))[3], 0)


if __name__ == "__main__":
    unittest.main()
