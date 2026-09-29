import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from account_switcher import claude_locks


class ClaudeLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.config_dir, self.config_file = self.home / ".claude", self.home / ".claude.json"
        self.config_dir.mkdir()
        self.locks = [self.config_dir / ".oauth_refresh.lock", self.home / ".claude.lock", self.home / ".claude.json.lock"]

    def tearDown(self):
        self.tmp.cleanup()

    def test_holds_claude_codes_three_locks_and_lets_go(self):
        with claude_locks.held(self.config_dir, self.config_file):
            self.assertTrue(all(lock.is_dir() for lock in self.locks))
        self.assertFalse(any(lock.exists() for lock in self.locks))

    def test_waits_for_a_live_holder(self):
        self.locks[1].mkdir()
        threading.Timer(0.5, self.locks[1].rmdir).start()
        start = time.monotonic()
        with claude_locks.held(self.config_dir, self.config_file, wait=5):
            waited = time.monotonic() - start
        self.assertGreater(waited, 0.4)

    def test_takes_over_a_stale_lock(self):
        self.locks[0].mkdir()
        old = time.time() - 120
        os.utime(self.locks[0], (old, old))
        with claude_locks.held(self.config_dir, self.config_file, wait=1):
            pass
        self.assertFalse(self.locks[0].exists())

    def test_gives_up_on_a_lock_held_too_long_and_leaves_it_alone(self):
        self.locks[0].mkdir()
        with self.assertRaises(claude_locks.Busy):
            with claude_locks.held(self.config_dir, self.config_file, wait=0.5):
                pass
        self.assertTrue(self.locks[0].exists())  # still Claude Code's
        self.assertFalse(self.locks[1].exists())


if __name__ == "__main__":
    unittest.main()
