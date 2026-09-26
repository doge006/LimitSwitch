import unittest
from types import SimpleNamespace as R

from account_switcher.placement import panel_contains, place_above, place_menu

MONITOR = R(left=0, top=0, right=1920, bottom=1080)


def work_for(edge):
    return {"bottom": R(left=0, top=0, right=1920, bottom=1032), "top": R(left=0, top=48, right=1920, bottom=1080),
            "left": R(left=48, top=0, right=1920, bottom=1080), "right": R(left=0, top=0, right=1872, bottom=1080)}[edge]


class PlacementTests(unittest.TestCase):
    def inside(self, x, y, w, h, work):
        return work.left <= x and work.top <= y and x + w <= work.right and y + h <= work.bottom

    def test_panel_never_overlaps_the_taskbar(self):
        for edge in ("bottom", "top", "left", "right"):
            for scale in (1.0, 1.5, 2.0):
                work = work_for(edge)
                w, h = round(440 * scale), round(460 * scale)
                for anchor in ((1880, 1056), (20, 1056), (960, 1056), (1896, 20), (24, 540)):
                    x, y, _ = place_above(anchor, MONITOR, work, w, h, scale)
                    self.assertTrue(self.inside(x, y, w, h, work), (edge, scale, anchor, x, y))

    def test_panel_is_centred_over_the_icon_and_against_the_taskbar(self):
        work = work_for("bottom")
        x, y, slide = place_above((960, 1056), MONITOR, work, 440, 560, 1.0)
        self.assertEqual((x + 220, y + 560), (960, 1032))
        self.assertEqual(slide, (0, 1))

    def test_menu_never_covers_the_clicked_point(self):
        work = work_for("bottom")
        for point in ((1880, 1050), (10, 1050), (960, 500)):
            px, py = point
            x, y, _ = place_menu(point, MONITOR, work, 268, 400, 1.0)
            self.assertTrue(self.inside(x, y, 268, 400, work))
            covers = x <= px < x + 268 and y <= py < y + 400
            self.assertFalse(covers and py < work.bottom and (px, py) != (960, 500), point)

    def test_shadow_margin_is_not_the_panel(self):
        self.assertFalse(panel_contains(5, 5, 440, 560))
        self.assertTrue(panel_contains(220, 280, 440, 560))
