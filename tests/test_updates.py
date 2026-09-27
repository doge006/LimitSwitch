import unittest
from unittest import mock

from account_switcher import updates, version
from account_switcher.web import Controller


class VersionTests(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(version.newer("v1.0.1", "1.0.0"))
        self.assertTrue(version.newer("1.10.0", "1.9.9"))
        self.assertFalse(version.newer("1.0.0", "1.0.0"))
        self.assertFalse(version.newer("v0.9", "1.0.0"))
        self.assertFalse(version.newer("", "1.0.0"))


class UpdateCheckTests(unittest.TestCase):
    def release(self, tag):
        return {"version": tag, "url": "https://example.invalid/r", "setup": "https://example.invalid/LimitSwitch-Setup.exe", "notes": ""}

    def test_newer_release_is_available(self):
        with mock.patch.object(updates, "latest_release", return_value=self.release("99.0.0")):
            state = updates.check()
        self.assertTrue(state["available"])
        self.assertEqual(state["latest"], "99.0.0")

    def test_same_version_is_up_to_date(self):
        with mock.patch.object(updates, "latest_release", return_value=self.release(version.VERSION)):
            self.assertFalse(updates.check()["available"])

    def test_offline_is_an_error_not_a_crash(self):
        with mock.patch.object(updates, "latest_release", side_effect=OSError("offline")):
            state = updates.check()
        self.assertFalse(state["available"])
        self.assertEqual(state["error"], "Couldn't reach GitHub")

    def test_controller_announces_a_new_version_once(self):
        controller = Controller()
        seen = []
        controller.on_update_available = seen.append
        try:
            with mock.patch.object(updates, "latest_release", return_value=self.release("99.0.0")):
                controller.check_updates()
                controller.check_updates()
            self.assertEqual(seen, ["99.0.0"])
            self.assertTrue(controller.snapshot()["update"]["available"])
        finally:
            controller.close()


if __name__ == "__main__":
    unittest.main()
