import unittest
from account_switcher.core import Account, Recovery, Router, demo_accounts


class RoutingTests(unittest.TestCase):
    def test_exhausted_account_fails_over_same_provider(self):
        router = Router(demo_accounts())
        router.exhaust(router.current("claude"))
        self.assertEqual(router.fallback("claude").id, "claude-b")
        self.assertEqual(router.current("codex").id, "codex-a")

    def test_no_auto_swap_when_disabled(self):
        router = Router(demo_accounts())
        router.auto_swap = False
        router.exhaust(router.current("claude"))
        self.assertIsNone(router.fallback("claude"))
        router.swap("claude-b")
        self.assertEqual(router.current("claude").id, "claude-b")

    def test_all_exhausted_stops_and_timestamp_does_not_reset(self):
        router = Router(demo_accounts())
        for account in router.accounts:
            router.exhaust(account)
            account.reset_at = 0
        self.assertIsNone(router.fallback("claude"))
        with self.assertRaises(ValueError):
            router.swap("claude-a")


    def test_fallback_prefers_most_headroom(self):
        router = Router(demo_accounts() + [
            Account(
                "claude-c", "claude", "Claude · Third", 95, 10, 0, 0)])
        router.exhaust(router.current("claude"))
        self.assertEqual(router.fallback("claude").id, "claude-b")

    def test_windows_include_model_caps(self):
        account = demo_accounts()[0]
        labels = [w["label"] for w in account.windows()]
        self.assertEqual(labels[:2], ["5-hour", "Weekly"])
        self.assertIn("Weekly · Fable", labels)


class RecoveryTests(unittest.TestCase):
    def failure(self, key="result-1"):
        return {"type": "result", "is_error": True, "uuid": key, "subtype": "success"}

    def test_opt_in_failure_evidence_and_dedup(self):
        recovery = Recovery()
        self.assertFalse(recovery.should_continue(self.failure(), True))
        recovery.enable(True)
        self.assertFalse(recovery.should_continue(self.failure("unrelated"), False))
        self.assertTrue(recovery.should_continue(self.failure("new"), True))
        self.assertFalse(recovery.should_continue(self.failure("new"), True))

    def test_never_continue_success_or_tool_ambiguity(self):
        recovery = Recovery()
        recovery.enable(True)
        self.assertFalse(recovery.should_continue({"type": "result", "is_error": False, "uuid": "ok"}, True))
        recovery.blocked_tools = True
        self.assertFalse(recovery.should_continue(self.failure(), True))

    def test_bounded_retries_and_cancel(self):
        recovery = Recovery()
        recovery.enable(True)
        for index in range(3):
            self.assertTrue(recovery.should_continue(self.failure(str(index)), True))
            recovery.submitted()
        self.assertFalse(recovery.should_continue(self.failure("too-many"), True))
        recovery = Recovery()
        recovery.enable(True)
        recovery.cancelled = True
        self.assertFalse(recovery.should_continue(self.failure(), True))


if __name__ == "__main__":
    unittest.main()
