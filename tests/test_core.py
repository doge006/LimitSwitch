import unittest
from account_switcher.core import Router, demo_accounts


class RoutingTests(unittest.TestCase):
    def test_swap_moves_one_provider(self):
        router = Router(demo_accounts())
        router.swap("claude-b")
        self.assertEqual(router.current("claude").id, "claude-b")
        self.assertEqual(router.current("codex").id, "codex-a")

    def test_used_up_account_cannot_be_picked(self):
        router = Router(demo_accounts())
        account = next(a for a in router.accounts if a.id == "claude-b")
        account.exhausted = True
        with self.assertRaises(ValueError):
            router.swap("claude-b")

    def test_windows_include_model_caps(self):
        account = demo_accounts()[0]
        labels = [w["label"] for w in account.windows()]
        self.assertEqual(labels[:2], ["5-hour", "Weekly"])
        self.assertIn("Weekly · Fable", labels)


if __name__ == "__main__":
    unittest.main()
