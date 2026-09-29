import unittest

from account_switcher import flyout_render as fr
from account_switcher import taskbar_layout as tl
from account_switcher.web import Controller


def demo_state():
    controller = Controller()
    try:
        return controller.snapshot()
    finally:
        controller.close()


class LayoutTests(unittest.TestCase):
    def test_default_is_what_the_taskbar_always_showed(self):
        self.assertEqual(tl.layout({}), {"main": ["claude", "codex"]})
        self.assertEqual(tl.layout({"taskbarDisplay": "right"}), {"right": ["claude", "codex"]})
        self.assertEqual(tl.layout({"taskbarLayout": {}}), {})  # every slot turned off

    def test_bad_saved_layouts_fall_back_to_the_default(self):
        for bad in ({"main": ["claude"]}, {"main": ["x", None]}, {"": ["claude", None]}, ["claude"], {"main": "codex"}):
            self.assertEqual(tl.layout({"taskbarLayout": bad}), {"main": ["claude", "codex"]}, bad)

    def test_a_click_cycles_every_choice_then_off(self):
        state = {"accounts": [{"id": "a", "provider": "claude"}, {"id": "b", "provider": "codex"}]}
        seen, slot = [], "claude"
        for _ in range(5):
            seen.append(slot)
            slot = tl.next_slot(state, slot)
        self.assertEqual(seen, ["claude", "codex", "acct:a", "acct:b", None])
        self.assertEqual(slot, "claude")  # and round again
        self.assertEqual(tl.next_slot(state, "acct:gone"), "claude")  # a removed account's slot

    def test_changing_a_slot_and_dropping_an_empty_display(self):
        current = {"main": ["claude", "codex"]}
        self.assertEqual(tl.with_slot(current, "left", 1, "acct:a"), {"main": ["claude", "codex"], "left": [None, "acct:a"]})
        self.assertEqual(tl.with_slot({"main": [None, "codex"]}, "main", 1, None), {})
        self.assertEqual(current, {"main": ["claude", "codex"]})  # not changed in place

    def test_the_tray_menus_display_choice_moves_everything_there(self):
        self.assertEqual(tl.moved({"main": [None, None], "left": ["codex", "acct:a"]}, "right"),
                         {"right": ["codex", "acct:a"]})

    def test_labels_follow_name_mode(self):
        state = {"accounts": [{"id": "a", "provider": "claude", "email": "a@example.com", "name": "Work"}]}
        self.assertEqual(tl.label(state, "acct:a"), "Work")
        self.assertEqual(tl.label(state, "claude"), "Claude in use")
        self.assertEqual(tl.label(state, None), "Off")
        self.assertEqual(tl.label(state, "acct:x"), "Removed account")


class BlockTests(unittest.TestCase):
    def test_a_block_for_one_account_shows_it_whatever_is_in_use(self):
        state = demo_state()
        other = next(a for a in state["accounts"] if a["provider"] == "claude" and not a["active"])
        layout, width = fr.build_block(state, "acct:" + other["id"])
        self.assertIn(fr.display_name(other), [t[2] for t in layout.texts])
        self.assertGreater(width, 0)
        self.assertEqual(fr.block_width(state, "acct:missing"), 0)  # removed: no block
        image, _ = fr.render_block(state, "acct:" + other["id"], scale=1.25)
        self.assertGreater(image.size[0], 0)


class SettingsTests(unittest.TestCase):
    def test_the_controller_keeps_a_layout_and_rejects_bad_ones(self):
        controller = Controller()
        try:
            controller.action("taskbar", {"layout": {"main": ["codex", "acct:claude-b"], "left": [None, "claude"]}})
            self.assertEqual(controller.snapshot()["taskbarLayout"], {"main": ["codex", "acct:claude-b"], "left": [None, "claude"]})
            with self.assertRaises(ValueError):
                controller.action("taskbar", {"layout": {"main": ["nope", None]}})
            controller.action("taskbar", {"display": "left"})  # the tray menu: all of it on the left display
            self.assertEqual(controller.snapshot()["taskbarLayout"], {"left": ["codex", "acct:claude-b"]})
            controller.action("taskbar", {"layout": {}})
            self.assertEqual(tl.layout(controller.snapshot()), {})
        finally:
            controller.close()

    def test_the_settings_menu_cycles_a_displays_slot(self):
        from tests.test_fullview import FullViewTests, state
        case = FullViewTests("test_settings_menu_toggles_preferences")
        case.setUp()
        displays = [{"id": "main", "label": "Main display"}, {"id": "left", "label": "Left display"}]
        case.view.set_state(state(taskbarAvailable=True, taskbar=True, taskbarDisplays=displays,
                                  taskbarLayout={"main": ["claude", None]}))
        case.view.frame()
        case.click("settings")
        case.click("slot:main:1")  # the main display's right slot: Off, then round to Claude in use
        case.click("slot:left:0")  # the left display (nothing yet): its left slot
        layouts = [body["layout"] for name, body in case.controller.calls if name == "taskbar"]
        self.assertEqual(layouts, [{"main": ["claude", "claude"]}, {"main": ["claude", None], "left": ["claude", None]}])

if __name__ == "__main__":
    unittest.main()
