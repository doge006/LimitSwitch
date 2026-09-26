"""The switching verifier, end to end against fake logins, a fake API and a stand-in CLI."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
from account_switcher.live import LiveAccounts
from account_switcher.providers import Claude, Codex
from account_switcher.vault import Vault
from account_switcher import verify
from tests.test_live import FakeAPI, codex_login, codex_usage, point_at_fake


class VerifyTests(unittest.TestCase):
    def test_full_check_passes_and_prompts_run_on_the_right_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            api = FakeAPI()
            try:
                claude, codex = Claude(home=home), Codex(home=home)
                point_at_fake(claude, codex, api)
                codex_login(home, "acct-2", "two@example.com", "at-2", "rt-2")
                api.codex_usage["at-2"] = codex_usage(10, 20)
                manager = LiveAccounts(lambda *_: None, Vault(root / "store"), {"claude": claude, "codex": codex})
                manager.sync_live()
                codex_login(home, "acct-1", "one@example.com", "at-1", "rt-1")
                api.codex_usage["at-1"] = codex_usage(30, 40)
                manager.sync_live()
                # Stand-in CLI: prints OK plus the email of whichever login it finds.
                log = root / "cli.log"
                script = root / "fake_cli.py"
                script.write_text(
                    "import json,sys,base64\n"
                    f"auth=json.load(open(r'{codex.auth_file}'))\n"
                    "p=auth['tokens']['id_token'].split('.')[1]; p+='='*(-len(p)%4)\n"
                    "email=json.loads(base64.urlsafe_b64decode(p))['email']\n"
                    f"open(r'{log}','a').write(email+'\\n'); print('OK')\n")
                report = verify.Report()
                verify.verify(manager, "codex", [sys.executable, str(script)], report)
                manager.spacing = 0
                self.assertFalse(report.failed, "\n".join(report.lines))
                ran_on = log.read_text().split()
                self.assertEqual(len(ran_on), 2)
                self.assertNotEqual(ran_on[0], ran_on[1])  # second prompt ran on the auto-swapped account
                self.assertEqual(codex.read_live().email, "one@example.com")  # back where we started
                text = "\n".join(report.lines)
                self.assertNotIn("at-1", text)  # no tokens in the report
            finally:
                api.close()

    def test_needs_two_accounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            home.mkdir()
            api = FakeAPI()
            try:
                claude, codex = Claude(home=home), Codex(home=home)
                point_at_fake(claude, codex, api)
                codex_login(home, "acct-1", "one@example.com", "at-1", "rt-1")
                manager = LiveAccounts(lambda *_: None, Vault(root / "store"), {"claude": claude, "codex": codex})
                report = verify.Report()
                verify.verify(manager, "codex", ["unused"], report)
                self.assertTrue(report.failed)
                self.assertIn("Add account", "\n".join(report.lines))
            finally:
                api.close()


if __name__ == "__main__":
    unittest.main()
