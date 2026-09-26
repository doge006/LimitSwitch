"""Runs the real installed Claude CLI against synthetic local accounts."""
import queue
import shutil
import time
import unittest

from account_switcher.client import ClaudeSession
from account_switcher.demo import DemoGateway


@unittest.skipUnless(shutil.which("claude"), "Claude CLI required")
class OfficialClientTests(unittest.TestCase):
    def run_scenario(self, mode, afk=False, auto=True):
        events = queue.Queue()
        notify = lambda kind, value: events.put((kind, value))
        gateway = DemoGateway(notify)
        gateway.router.auto_swap = auto
        url = gateway.start()
        session = ClaudeSession(gateway, notify)
        results, states = [], []
        try:
            gateway.arm(mode)
            session.start(url)
            session.recovery.enable(afk)
            session.send("Say hello.")
            deadline = time.monotonic() + 35
            while time.monotonic() < deadline:
                try:
                    kind, value = events.get(timeout=1)
                except queue.Empty:
                    continue
                if kind == "result":
                    results.append(value)
                if kind == "state":
                    states.append(value)
                    if value == "Completed" or value.startswith(("Interrupted", "Waiting")):
                        break
            else:
                self.fail("Client did not settle within 35 seconds")
            return results, states, list(gateway.requests), session.recovery.attempts, gateway.router.active.copy()
        finally:
            session.close()
            gateway.close()

    def test_quota_before_output_uses_second_account(self):
        results, _, _, attempts, active = self.run_scenario("quota")
        self.assertEqual([r["failed"] for r in results], [False])
        self.assertEqual(active["claude"], "claude-b")
        self.assertEqual(attempts, 0)

    def test_partial_failure_afk_continues_same_session(self):
        results, _, requests, attempts, active = self.run_scenario("partial", afk=True)
        self.assertEqual([r["failed"] for r in results], [True, False])
        self.assertEqual(len({r["session_id"] for r in results}), 1)
        self.assertEqual(attempts, 1)
        self.assertEqual(requests[0]["account"], "claude-a")
        self.assertEqual(requests[-1]["account"], "claude-b")
        self.assertEqual(active["claude"], "claude-b")

    def test_afk_disabled_does_not_submit_continue(self):
        results, states, _, attempts, _ = self.run_scenario("partial")
        self.assertEqual([r["failed"] for r in results], [True])
        self.assertTrue(states[-1].startswith("Interrupted"))
        self.assertEqual(attempts, 0)

    def test_auto_swap_disabled_waits(self):
        results, states, _, attempts, active = self.run_scenario("partial", afk=True, auto=False)
        self.assertEqual([r["failed"] for r in results], [True])
        self.assertTrue(states[-1].startswith("Waiting"))
        self.assertEqual(attempts, 0)
        self.assertEqual(active["claude"], "claude-a")


if __name__ == "__main__":
    unittest.main()
