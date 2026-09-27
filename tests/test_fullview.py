import time
import unittest

try:
    from account_switcher import fullview, fullview_render as vr
except ImportError as error:  # Pillow missing
    raise unittest.SkipTest(f"full view dependencies missing: {error}")


def account(i, provider, active=False, eligible=True, **extra):
    now = time.time()
    view = {"id": f"{provider}-{i}", "provider": provider, "alias": f"a{i}", "email": f"user{i}@example.com",
            "name": f"user{i}@example.com", "label": "", "plan": "Pro", "active": active, "eligible": eligible,
            "status": None, "subscription": None, "credits": None,
            "windows": [{"key": "five_hour", "label": "5-hour limit", "used": 40, "resetsAt": now + 3600, "scope": "account"}]}
    view.update(extra)
    return view


def state(**extra):
    base = {"revision": 1, "mode": "live", "busy": False, "afk": False, "autoSwap": True, "nameMode": False,
            "taskbar": True, "taskbarAvailable": False, "log": [], "signingIn": [],
            "accounts": [account(1, "claude", active=True), account(2, "claude"), account(1, "codex", active=True)]}
    base.update(extra)
    return base


class Host:
    def __init__(self):
        self.timers, self.invalidated, self.cursor, self.paste = {}, 0, "arrow", ""

    def invalidate(self):
        self.invalidated += 1

    def set_timer(self, name, ms):
        self.timers[name] = ms

    def kill_timer(self, name):
        self.timers.pop(name, None)

    def has_timer(self, name):
        return name in self.timers

    def set_cursor(self, kind):
        self.cursor = kind

    def clipboard(self):
        return self.paste


class Controller:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def action(self, name, body):
        if name == self.fail:
            raise RuntimeError("An operation is already running")
        self.calls.append((name, body))


class FullViewTests(unittest.TestCase):
    def setUp(self):
        self.controller, self.host = Controller(), Host()
        self.view = fullview.FullView(self.controller, self.host, state())
        self.view.resize(960, 900, 1.0)
        self.view.frame()

    def click(self, action):
        box = next(h[0] for h in self.view.page_hits + [(b, a, c, None) for box, hits in self.view.overlay_hits
                                                         for b, a, c in hits] if h[1] == action)
        x, y = box[0] + box[2] / 2, box[1] + box[3] / 2
        if not any(h[1] == action for h in self.view.page_hits):
            y += self.view.scroll  # overlay hits are in window coordinates
        y -= self.view.scroll
        self.view.mouse_move(x, y)
        self.view.mouse_down(x, y)
        self.view.mouse_up(x, y)
        self.view.frame()

    def test_asks_for_fresh_usage_when_opened(self):
        self.assertEqual(self.controller.calls[0], ("refresh", {"ifOlderThan": 60}))

    def test_swap_button_switches_and_shows_progress(self):
        self.click("swap:claude-2")
        self.assertIn(("swap", {"id": "claude-2"}), self.controller.calls)
        self.assertEqual(self.view.ui.pending, "claude-2")
        self.assertIn("pending", self.host.timers)
        landed = state(accounts=[account(1, "claude"), account(2, "claude", active=True), account(1, "codex", active=True)])
        self.view.set_state(landed)
        self.assertIsNone(self.view.ui.pending)

    def test_no_swap_button_on_the_account_in_use(self):
        self.assertFalse(any(h[1] == "swap:claude-1" for h in self.view.page_hits))

    def test_settings_menu_toggles_preferences(self):
        self.click("settings")
        self.assertEqual(self.view.ui.menu, "settings")
        self.click("set:afk")
        self.assertIn(("preferences", {"autoSwap": True, "afk": True}), self.controller.calls)
        self.click("set:nameMode")
        self.assertIn(("names", {"on": True}), self.controller.calls)

    def test_remove_asks_first(self):
        self.view.mouse_move(700, 200)  # over the second Claude card: its Remove button fades in
        self.view.frame()
        self.view.motion.settle()
        self.view.frame()
        self.click("remove:claude-2")
        self.assertFalse(any(c[0] == "remove" for c in self.controller.calls))
        self.click("remove-yes:claude-2")
        self.assertIn(("remove", {"id": "claude-2"}), self.controller.calls)

    def test_rename_in_name_mode(self):
        self.view.set_state(state(nameMode=True))
        self.view.frame()
        self.click("name:claude-2")
        for ch in "Work":
            self.view.char(ch)
        self.view.key("enter")
        self.assertIn(("rename", {"id": "claude-2", "name": "Work"}), self.controller.calls)

    def test_renewal_date_editor_saves_the_picked_day(self):
        self.click("renew:claude-2")
        self.assertIsNotNone(self.view.ui.editor)
        self.click("ed-kind:ends")
        self.click("ed-day:15")
        self.click("ed-save")
        name, body = self.controller.calls[-1]
        self.assertEqual(name, "subscription")
        self.assertTrue(body["ends"])
        self.assertEqual(time.localtime(body["at"]).tm_mday, 15)
        self.assertIsNone(self.view.ui.editor)

    def test_errors_become_toasts(self):
        view = fullview.FullView(Controller(fail="swap"), self.host, state())
        view.resize(960, 900, 1.0)
        view.frame()
        view.activate("swap:claude-2")
        self.assertIsNone(view.ui.pending)
        self.assertEqual(view.toast_list[-1][:2], ("An operation is already running", "error"))

    def test_scrolls_within_the_page(self):
        many = state(accounts=[account(i, "claude") for i in range(12)])
        self.view.set_state(many)
        self.view.wheel(10_000)
        self.assertEqual(self.view.scroll, self.view.max_scroll())
        self.assertGreater(self.view.scroll, 0)
        self.view.wheel(-10_000)
        self.assertEqual(self.view.scroll, 0)

    def test_unchanged_cards_are_not_redrawn(self):
        before = {key: tile for key, (_, tile) in self.view.tiles.items()}
        self.view.mouse_move(700, 200)  # hover one card
        self.view.frame()
        redrawn = [key for key, (_, tile) in self.view.tiles.items() if before.get(key) is not tile]
        self.assertEqual(redrawn, ["card:claude-2"])

    def test_animates_only_while_something_moves(self):
        self.view.frame()
        self.assertIn("anim", self.host.timers)  # cards rising in, bars filling
        self.view.motion.settle()
        self.view.frame()
        self.assertNotIn("anim", self.host.timers)  # at rest: no frames at all
        self.view.mouse_move(700, 200)
        self.view.frame()
        self.assertIn("anim", self.host.timers)  # the hover fades in

    def test_bars_fill_from_empty_then_follow_changes(self):
        motion = self.view.motion
        bar = ("bar", ("claude-2", "five_hour"))
        self.assertIn(bar, motion.runs)
        self.assertEqual(motion.runs[bar][:2], (0.0, 60))  # from empty to 60% left
        motion.settle()
        changed = state()
        changed["accounts"][1]["windows"][0]["used"] = 90
        self.view.set_state(changed)
        self.view.frame()
        self.assertEqual(motion.runs[bar][:2], (60, 10))

    def test_closing_frees_the_tiles(self):
        self.view.close()
        self.assertEqual(self.view.tiles, {})

    def test_frame_is_window_sized(self):
        self.view.resize(800, 600, 1.5)
        image = self.view.frame()
        self.assertEqual(image.size, (1200, 900))


class HelperTests(unittest.TestCase):
    def test_redact_keeps_first_letter_and_domain(self):
        self.assertEqual(vr.redact("dogebuns5@gmail.com"), "d********@gmail.com")

    def test_columns_follow_the_width(self):
        self.assertEqual(vr.columns_for(960)[0], 2)
        self.assertEqual(vr.columns_for(600)[0], 1)

    def test_credits_line(self):
        self.assertEqual(vr.credits_items({"credits": {"kind": "credits", "balance": 12.5, "resets": 2}}),
                         [("Credits", "12.5"), ("Usage limit resets", "2")])


if __name__ == "__main__":
    unittest.main()
