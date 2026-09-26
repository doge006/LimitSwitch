import os
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

os.environ["PYSTRAY_BACKEND"] = "dummy"  # menu/icon logic only; no desktop needed
try:
    import pystray
    from account_switcher import tray
except ImportError as error:  # pystray/Pillow not installed
    raise unittest.SkipTest(f"tray dependencies missing: {error}")
from account_switcher.web import Controller, make_server


class FakeIcon:
    def __init__(self, name, icon, title, menu):
        self.icon, self.title, self.menu = icon, title, menu
        self.notes, self.menu_updates, self.stopped = [], 0, False

    def notify(self, message, title=None):
        self.notes.append((title, message))

    def update_menu(self):
        self.menu_updates += 1

    def stop(self):
        self.stopped = True


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.02)
    return False


class TrayTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(simulator=True)
        self.server = make_server(self.controller, idle_seconds=0)
        self.tray = tray.Tray(self.controller, self.server, icon_factory=FakeIcon)
        self.icon = self.tray.icon

    def tearDown(self):
        self.controller.close()
        self.server.server_close()

    def settle(self):
        self.assertTrue(wait_for(lambda: not self.controller.snapshot()["busy"]))
        self.tray.refresh()

    def test_tooltip_and_status_dot(self):
        state = self.controller.snapshot()
        text = tray.tooltip(state)
        self.assertLessEqual(len(text), 127)
        self.assertIn("Claude: Personal · 36% left", text)
        self.assertIn("Codex: Personal · 49% left", text)
        self.assertEqual(tray.tray_level(state), "good")
        state["accounts"][0]["eligible"] = False
        self.assertEqual(tray.tray_level(state), "bad")
        self.assertIn("limit reached", tray.tooltip(state))
        self.assertEqual(tray.icon_image("warn").size, (64, 64))

    def test_menu_lists_accounts_and_switches(self):
        items = list(self.tray.menu_items())
        texts = [i.text for i in items if i is not pystray.Menu.SEPARATOR]
        self.assertEqual(texts[0], "Open Account Switcher")
        self.assertTrue(items[0].default)
        self.assertIn("Claude", texts)
        self.assertIn("Quit", texts)
        radios = [i for i in items if i is not pystray.Menu.SEPARATOR and i.radio]
        self.assertEqual(len(radios), 4)
        self.assertEqual(sum(i.checked for i in radios), 2)
        header = next(i for i in items if i is not pystray.Menu.SEPARATOR and i.text == "Claude")
        self.assertFalse(header.enabled)

    def test_menu_actions_swap_and_toggle(self):
        second = next(i for i in self.tray.menu_items() if getattr(i, "text", "").startswith("Second") and i.radio)
        second(self.icon)
        self.settle()
        self.assertEqual(self.controller.gateway.router.active["claude"], "claude-b")
        self.assertEqual(self.icon.notes, [])  # manual swaps are not announced
        afk = next(i for i in self.tray.menu_items() if getattr(i, "text", "") == "AFK mode")
        afk(self.icon)
        self.settle()
        self.assertTrue(self.controller.afk)
        self.assertIn("AFK", self.icon.title)

    def test_refresh_only_touches_what_changed(self):
        self.tray.refresh()
        updates, title = self.icon.menu_updates, self.icon.title
        self.tray.refresh()
        self.assertEqual(self.icon.menu_updates, updates)
        self.assertEqual(self.icon.title, title)

    def test_failover_is_announced(self):
        self.tray.refresh()
        router = self.controller.gateway.router
        router.exhaust(router.current("claude"))
        router.fallback("claude")
        self.tray.refresh()
        self.assertEqual(len(self.icon.notes), 1)
        title, message = self.icon.notes[0]
        self.assertEqual(title, "Claude switched accounts")
        self.assertIn("Personal hit its limit", message)
        self.assertIn("Second", message)

    def test_watch_follows_changes_and_quits_with_controller(self):
        thread = threading.Thread(target=self.tray.watch, daemon=True)
        thread.start()
        self.controller.action("swap", {"id": "codex-b"})
        self.assertTrue(wait_for(lambda: "Codex: Second" in self.icon.title))
        self.controller.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(self.icon.stopped)

    def test_dashboard_shutdown_quits_tray(self):
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": .1}, daemon=True)
        thread.start()
        try:
            parsed = urlsplit(self.server.launch_url)
            token = parse_qs(parsed.fragment)["token"][0]
            request = Request(f"http://{parsed.netloc}/api/shutdown", b"{}",
                              {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
            with build_opener(ProxyHandler({})).open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            self.assertTrue(wait_for(lambda: self.icon.stopped))
        finally:
            self.server.shutdown()

    def test_no_idle_threads_without_timeout(self):
        before = {t.name for t in threading.enumerate()}
        server = make_server(self.controller, idle_seconds=0)
        try:
            self.assertEqual({t.name for t in threading.enumerate()} - before, set())
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
