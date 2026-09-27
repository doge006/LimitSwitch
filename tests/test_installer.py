"""The installer's platform-independent parts: updating the checkout safely, the macOS app
bundle and login item. Uses throwaway git repositories; nothing is installed."""
import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def load_installer():
    spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts" / "installer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com"}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.origin, self.clone, self.other = base / "origin.git", base / "clone", base / "other"
        run("git", "init", "--quiet", "--bare", "-b", "main", str(self.origin), cwd=base)
        run("git", "clone", "--quiet", str(self.origin), str(self.other), cwd=base)
        self.commit(self.other, "a.txt", "one")
        run("git", "push", "--quiet", "origin", "HEAD:main", cwd=self.other)
        run("git", "clone", "--quiet", str(self.origin), str(self.clone), cwd=base)
        self.installer = load_installer()
        self.installer.ROOT = self.clone

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def commit(self, repo, name, text):
        (repo / name).write_text(text)
        run("git", "add", name, cwd=repo)
        run("git", "commit", "--quiet", "-m", f"{name}: {text}", cwd=repo)

    def publish(self, text):
        self.commit(self.other, "a.txt", text)
        run("git", "push", "--quiet", "origin", "HEAD:main", cwd=self.other)

    def test_up_to_date_then_fast_forward(self):
        self.assertEqual(self.installer.update_code("", False)[0], "current")
        self.publish("two")
        self.assertEqual(self.installer.update_code("", False)[0], "updated")
        self.assertEqual((self.clone / "a.txt").read_text(), "two")

    def test_local_edits_block_unless_forced(self):
        self.publish("two")
        (self.clone / "a.txt").write_text("my edit")
        self.assertEqual(self.installer.update_code("", False)[0], "blocked")
        self.assertEqual((self.clone / "a.txt").read_text(), "my edit")
        self.assertEqual(self.installer.update_code("", True)[0], "updated")
        self.assertEqual((self.clone / "a.txt").read_text(), "two")
        self.assertIn("installer backup", self.installer.git("stash", "list"))  # the edit is kept

    def test_local_commits_are_backed_up_when_forced(self):
        self.commit(self.clone, "mine.txt", "local")
        self.publish("two")
        self.assertEqual(self.installer.update_code("", False)[0], "blocked")
        self.assertEqual(self.installer.update_code("", True)[0], "updated")
        branches = self.installer.git("branch", "--list", "backup/*")
        self.assertIn("backup/update-", branches)
        self.assertFalse((self.clone / "mine.txt").exists())


class MacBundleTests(unittest.TestCase):
    def test_app_bundle_is_menu_bar_only_and_starts_the_venv(self):
        installer = load_installer()
        with tempfile.TemporaryDirectory() as tmp:
            system, user = Path(tmp) / "system" / "LimitSwitcher.app", Path(tmp) / "user" / "LimitSwitcher.app"
            system.parent.mkdir()
            installer.MAC_APPS = (system, user)
            installer.build_mac_app(user)  # an older install in ~/Applications
            installer.mac_app()
            self.assertEqual(installer.MAC_APP, system)  # writable: /Applications
            self.assertFalse(user.exists())  # the old copy is removed
            info = plistlib.loads((system / "Contents" / "Info.plist").read_bytes())
            self.assertTrue(info["LSUIElement"])
            self.assertEqual(info["LSArchitecturePriority"][0], "arm64")  # not Rosetta on Apple silicon
            self.assertEqual(info["CFBundleExecutable"], "AccountSwitcher")
            launcher = system / "Contents" / "MacOS" / "AccountSwitcher"
            self.assertTrue(os.access(launcher, os.X_OK))
            text = launcher.read_text()
            self.assertIn("LimitSwitcher.pyw", text)
            self.assertIn('>>"$LOG" 2>&1', text)
            self.assertIn("arch -arm64", text)
            self.assertIn("--at-login", text)  # a failed start leaves its error in app.log
            self.assertTrue((system / "Contents" / "Resources" / "AppIcon.icns").exists())
            done = subprocess.run(["sh", "-n", str(launcher)], capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_prebuilt_launcher_when_the_build_tools_cannot_link(self):
        installer = load_installer()
        self.assertTrue(installer.PREBUILT_LAUNCHER.is_file())  # universal build from CI, kept in the repo
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "LimitSwitcher"
            self.assertTrue(installer.build_launcher(target, prebuilt_only=True))
            self.assertEqual(target.read_bytes(), installer.PREBUILT_LAUNCHER.read_bytes())
            self.assertTrue(os.access(target, os.X_OK))

    def test_login_item(self):
        from account_switcher import integrations
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(integrations.Path, "home", return_value=Path(tmp)):
            integrations._set_launch_agent(True)
            plist = plistlib.loads(integrations.launch_agent_path().read_bytes())
            self.assertTrue(plist["RunAtLoad"])
            self.assertTrue(plist["ProgramArguments"][1].endswith("LimitSwitcher.pyw"))
            app = Path(tmp) / "LimitSwitcher.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            (app / "Contents" / "MacOS" / "AccountSwitcher").write_text("")
            with mock.patch.dict(os.environ, {"ACCOUNT_SWITCHER_APP": str(app)}):
                integrations._set_launch_agent(True)  # started from the app: log in as the app
            plist = plistlib.loads(integrations.launch_agent_path().read_bytes())
            self.assertEqual(plist["ProgramArguments"], [str(app / "Contents" / "MacOS" / "AccountSwitcher"), "--at-login"])
            integrations._set_launch_agent(False)
            self.assertFalse(integrations.launch_agent_path().exists())


if __name__ == "__main__":
    unittest.main()
