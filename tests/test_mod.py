import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from account_switcher import mod
from account_switcher.web import Controller


def done(code=0, out="", err=""):
    return subprocess.CompletedProcess(["claude"], code, out, err)


class ModCommandTests(unittest.TestCase):
    def test_installed_reads_claude_codes_own_list(self):
        listing = json.dumps([{"id": "other@x"}, {"id": "limit-status@limitswitcher", "enabled": True}])
        with mock.patch.object(mod, "_run", return_value=done(out=listing)):
            self.assertTrue(mod.installed())
        with mock.patch.object(mod, "_run", return_value=done(out="[]")):
            self.assertFalse(mod.installed())

    def test_installed_is_unknown_without_claude_code(self):
        with mock.patch.object(mod, "_run", side_effect=FileNotFoundError("no claude")):
            self.assertIsNone(mod.installed())
        with mock.patch.object(mod, "_run", return_value=done(code=1)):
            self.assertIsNone(mod.installed())

    def test_install_adds_the_marketplace_then_the_plugin_with_the_state_file(self):
        calls = []
        with mock.patch.object(mod, "_run", side_effect=lambda args, timeout: calls.append(args) or done()):
            self.assertIsNone(mod.install("/data/afk-hook.json"))
        self.assertEqual(calls[0][:2], ["marketplace", "add"])
        self.assertEqual(calls[-1], ["install", "limit-status@limitswitcher", "--config", "statePath=/data/afk-hook.json"])

    def test_install_is_fine_when_already_there_and_says_why_when_not(self):
        with mock.patch.object(mod, "_run", return_value=done(code=1, err="Marketplace already exists")):
            self.assertIsNone(mod.install("/s"))
        with mock.patch.object(mod, "_run", return_value=done(code=1, err="network down")):
            self.assertEqual(mod.install("/s"), "network down")
        with mock.patch.object(mod, "_run", side_effect=FileNotFoundError("Claude Code isn't installed")):
            self.assertIn("isn't installed", mod.install("/s"))


class ModStateTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller()

    def tearDown(self):
        self.controller.close()

    def test_demo_mode_has_no_mod(self):
        self.assertEqual(self.controller.snapshot()["mod"], {"status": "unavailable"})

    def test_status_follows_reports_and_install(self):
        c = self.controller
        c.live = True
        c.mod_checked = time.time()  # no look-up thread in this test
        self.assertEqual(c.mod_state()["status"], "unknown")
        c.mod_installed = False
        self.assertEqual(c.mod_state()["status"], "missing")
        c.mod_installed = True
        self.assertEqual(c.mod_state()["status"], "installed")
        c.mod_seen = time.time()
        self.assertEqual(c.mod_state()["status"], "active")
        c.mod_seen = time.time() - 300  # no session reports any more
        self.assertEqual(c.mod_state()["status"], "installed")
        c.mod_busy = "installing"
        self.assertEqual(c.mod_state()["status"], "installing")
        c.mod_busy = "Install failed · see log"
        self.assertEqual(c.mod_state(), {"status": "error", "text": "Install failed · see log"})

    def test_a_report_from_the_mod_makes_it_active(self):
        c = self.controller
        c.live = True
        c.gateway = mock.Mock()
        c.gateway.manager.statusline.return_value = "line"
        c.statusline({"rate_limits": None, "session": "s1"})  # the status line script
        self.assertEqual(c.mod_seen, 0.0)
        c.statusline({"rate_limits": None, "session": "s1", "source": "mod"})
        self.assertGreater(c.mod_seen, 0.0)
        self.assertTrue(c.mod_installed)

    def test_the_line_is_shown_once_by_the_mod_when_it_is_active(self):
        c = self.controller
        c.live = True
        c.gateway = mock.Mock()
        c.gateway.manager.statusline.return_value = "line"
        c.gateway.manager.meta = {"statuslineShown": True}
        self.assertEqual(c.statusline({"session": "s"}), "line")  # no mod yet: the status line script shows it
        c.statusline({"session": "s", "source": "mod"})
        self.assertEqual(c.statusline({"session": "s", "source": "mod"}), "line")  # the mod's own
        self.assertIsNone(c.statusline({"session": "s"}))  # the script keeps reporting but stays quiet

    def test_the_mod_shows_its_line_even_with_the_status_line_switch_off(self):
        c = self.controller
        c.live = True
        c.gateway = mock.Mock()
        c.gateway.manager.statusline.return_value = "line"
        c.gateway.manager.meta = {"statuslineShown": False}  # the script's own switch
        self.assertIsNone(c.statusline({"session": "s"}))
        self.assertEqual(c.statusline({"session": "s", "source": "mod"}), "line")


if __name__ == "__main__":
    unittest.main()
