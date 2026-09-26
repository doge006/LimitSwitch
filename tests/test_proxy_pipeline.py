"""Installed Claude -> compiled fork -> two synthetic upstream accounts.

No real account credentials. Recovery restores a deliberately failed reserve
account in the fixture; that fault injection is separate from provider behavior.
"""
import queue
import shutil
import time
import unittest
from pathlib import Path

from account_switcher.client import ClaudeSession
from experiments.proxy.runtime import ProxyFixture


class ProxyBridge:
    def __init__(self, fixture):
        self.fixture = fixture
        self.token = fixture.api_key

    @property
    def quota_observed(self):
        return any(e["mode"] in ("quota", "partial") for e in self.fixture.events)

    def prepare_continue(self):
        self.fixture.settle_failure()
        return True


@unittest.skipUnless(shutil.which("claude") and Path("experiments/proxy/cli-proxy-api.exe").exists(), "Built proxy and Claude required")
class ActualProxyPipelineTests(unittest.TestCase):
    def run_case(self, partial):
        fixture = ProxyFixture().start()
        events = queue.Queue()
        session = ClaudeSession(ProxyBridge(fixture), lambda kind, value: events.put((kind, value)))
        results = []
        try:
            fixture.modes.update(A="partial" if partial else "quota", B="partial" if partial else "normal")
            session.start(fixture.base_url)
            session.recovery.enable(partial)
            session.send("Say hello briefly.")
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                try:
                    kind, value = events.get(timeout=1)
                except queue.Empty:
                    continue
                if kind == "result":
                    results.append(value)
                if kind == "state" and value == "Completed":
                    break
            else:
                self.fail("Real-client/proxy pipeline did not recover: " + repr(results))
            self.assertEqual([r["failed"] for r in results], [True, False] if partial else [False])
            self.assertEqual(len({r["session_id"] for r in results}), 1)
            inference = [e for e in fixture.events if "/count_tokens" not in e["path"]]
            self.assertEqual(inference[0]["account"], "A")
            self.assertEqual(inference[-1]["account"], "B")
            self.assertTrue(all(e["model"] == "claude-sonnet-4-6" for e in inference))
        finally:
            session.close()
            fixture.stop()

    def test_actual_proxy_before_output_failover(self):
        self.run_case(False)

    def test_actual_proxy_partial_afk_recovery(self):
        self.run_case(True)
