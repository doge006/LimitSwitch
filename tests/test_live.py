"""Real-account backend against fake Claude Code / Codex files and a fake provider API."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
from account_switcher.live import LiveAccounts, LiveGateway
from account_switcher.providers import Claude, Codex
from account_switcher.vault import Vault
from account_switcher.web import Controller


def jwt(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{body}.sig"


class FakeAPI:
    """Usage per access token; token endpoints rotate tokens and count calls."""

    def __init__(self):
        self.claude_usage, self.codex_usage = {}, {}
        self.refreshes = []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                token = self.headers.get("Authorization", "")[7:]
                table = api.claude_usage if self.path == "/claude/usage" else api.codex_usage
                if token not in table:
                    return self.reply(401, {"error": "expired"})
                self.reply(200, table[token])

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                api.refreshes.append((self.path, body["refresh_token"]))
                new = body["refresh_token"] + "+"
                if self.path == "/claude/token":
                    self.reply(200, {"access_token": "at-" + new, "refresh_token": new, "expires_in": 28800,
                                     "account": {"uuid": api.uuid_for.get(body["refresh_token"])}})
                else:
                    self.reply(200, {"access_token": "at-" + new, "refresh_token": new})

        self.uuid_for = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def claude_login(home, uuid, email, access, refresh, expires_in=3600, tier="default_claude_max_20x", extra=None):
    creds = {"claudeAiOauth": {"accessToken": access, "refreshToken": refresh, "subscriptionType": "max",
                               "rateLimitTier": tier, "expiresAt": int((time.time() + expires_in) * 1000)}}
    creds.update(extra or {})
    (home / ".claude").mkdir(exist_ok=True)
    (home / ".claude" / ".credentials.json").write_text(json.dumps(creds))
    config = json.loads((home / ".claude.json").read_text()) if (home / ".claude.json").exists() else {"theme": "dark"}
    config["oauthAccount"] = {"accountUuid": uuid, "emailAddress": email}
    (home / ".claude.json").write_text(json.dumps(config))


def codex_login(home, account_id, email, access, refresh, plan="pro"):
    (home / ".codex").mkdir(exist_ok=True)
    auth = {"tokens": {"access_token": access, "refresh_token": refresh, "account_id": account_id,
                       "id_token": jwt({"email": email, "https://api.openai.com/auth": {"chatgpt_plan_type": plan}})},
            "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (home / ".codex" / "auth.json").write_text(json.dumps(auth))


def claude_usage(five, week, fable=None, reset_in=3600):
    reset = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + reset_in))
    body = {"five_hour": {"utilization": five, "resets_at": reset}, "seven_day": {"utilization": week, "resets_at": reset}}
    if fable is not None:
        body["limits"] = [{"kind": "weekly_scoped", "percent": fable, "resets_at": reset,
                           "scope": {"model": {"display_name": "Fable 5"}}}]
    return body


def codex_usage(five, week, limit_reached=False):
    now = time.time()
    return {"plan_type": "pro", "rate_limit": {"limit_reached": limit_reached,
            "primary_window": {"used_percent": five, "reset_at": now + 3000, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": week, "reset_at": now + 86400, "limit_window_seconds": 604800}}}


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.api = FakeAPI()
        claude, codex = Claude(home=self.home), Codex(home=self.home)
        claude.USAGE_URL, claude.TOKEN_URL = self.api.base + "/claude/usage", self.api.base + "/claude/token"
        codex.USAGE_URL, codex.TOKEN_URL = self.api.base + "/codex/usage", self.api.base + "/codex/token"
        self.providers = {"claude": claude, "codex": codex}
        self.vault = Vault(root / "store")
        self.logs = []
        claude_login(self.home, "uuid-a", "a@example.com", "at-a", "rt-a", extra={"mcpOAuth": {"server": "keep"}})
        codex_login(self.home, "acct-x", "x@example.com", "at-x", "rt-x")
        self.api.claude_usage["at-a"] = claude_usage(40, 20, fable=10)
        self.api.codex_usage["at-x"] = codex_usage(5, 50)

    def tearDown(self):
        self.api.close()
        self.tmp.cleanup()

    def manager(self):
        return LiveAccounts(lambda kind, value: self.logs.append((kind, value)), self.vault, self.providers)

    def by_email(self, manager, email):
        return next(a for a in manager.accounts() if a.email == email)

    def live_claude(self):
        return self.providers["claude"].read_live()

    def test_imports_live_logins_and_reads_usage(self):
        m = self.manager()
        m.sync_live()
        self.assertEqual(sorted(a.email for a in m.accounts()), ["a@example.com", "x@example.com"])
        m.refresh()
        a = self.by_email(m, "a@example.com")
        self.assertEqual(a.plan, "Max 20x")
        self.assertEqual([(w["key"], w["used"], w["scope"]) for w in a.windows()],
                         [("five_hour", 40.0, "account"), ("weekly", 20.0, "account"), ("model-fable-5", 10.0, "model")])
        self.assertEqual(a.headroom, 60)
        self.assertIsNotNone(a.renews_at)
        x = self.by_email(m, "x@example.com")
        self.assertEqual([w["key"] for w in x.windows()], ["five_hour", "weekly"])
        self.assertEqual(x.plan, "Pro")
        self.assertEqual(self.api.refreshes, [])  # in-use accounts are never refreshed by us

    def test_new_login_is_added_and_switching_round_trips(self):
        m = self.manager()
        m.sync_live()
        claude_login(self.home, "uuid-b", "b@example.com", "at-b", "rt-b", tier="default_claude_max_5x",
                     extra={"mcpOAuth": {"server": "keep"}})
        m.sync_live()
        b = self.by_email(m, "b@example.com")
        self.assertEqual(m.active["claude"], b.id)
        self.assertEqual(b.plan, "Max 5x")
        # Claude Code rotates B's tokens while B is in use...
        creds = json.loads((self.home / ".claude" / ".credentials.json").read_text())
        creds["claudeAiOauth"]["refreshToken"] = "rt-b-rotated"
        creds["mcpOAuth"] = {"server": "newer"}
        (self.home / ".claude" / ".credentials.json").write_text(json.dumps(creds))
        a = self.by_email(m, "a@example.com")
        m.swap(a.id)
        live = self.live_claude()
        self.assertEqual(live.email, "a@example.com")
        self.assertEqual(live.secret["credentials"]["claudeAiOauth"]["refreshToken"], "rt-a")
        self.assertEqual(live.secret["credentials"]["mcpOAuth"], {"server": "newer"})  # MCP logins untouched
        self.assertEqual(json.loads((self.home / ".claude.json").read_text())["theme"], "dark")  # other config kept
        # ...and switching back restores B with the rotated token we captured.
        m.swap(b.id)
        self.assertEqual(self.live_claude().secret["credentials"]["claudeAiOauth"]["refreshToken"], "rt-b-rotated")

    def test_inactive_accounts_refresh_but_live_one_waits(self):
        m = self.manager()
        m.sync_live()
        claude_login(self.home, "uuid-b", "b@example.com", "at-b-old", "rt-b", expires_in=-10)
        self.api.uuid_for["rt-b"] = "uuid-b"
        m.sync_live()
        m.refresh()
        b = self.by_email(m, "b@example.com")
        self.assertIn("Waiting for Claude", b.status)  # live login expired: leave it to Claude Code
        self.assertEqual([r for r in self.api.refreshes if r[0] == "/claude/token"], [])
        a = self.by_email(m, "a@example.com")
        m.swap(a.id)  # now B is inactive and its expired token is ours to refresh
        self.api.claude_usage["at-rt-b+"] = claude_usage(10, 10)
        m.refresh()
        self.assertEqual(self.api.refreshes, [("/claude/token", "rt-b")])
        b = self.by_email(m, "b@example.com")
        self.assertEqual(b.status, "")
        self.assertEqual(self.vault.read_secret(b.id)["credentials"]["claudeAiOauth"]["refreshToken"], "rt-b+")

    def test_auto_swap_moves_to_most_headroom(self):
        m = self.manager()
        m.sync_live()
        for uuid, email, access, five in (("uuid-b", "b@example.com", "at-b", 70), ("uuid-c", "c@example.com", "at-c", 10)):
            claude_login(self.home, uuid, email, access, "rt-" + uuid, expires_in=36000)
            self.api.claude_usage[access] = claude_usage(five, 5)
            m.sync_live()
        m.swap(self.by_email(m, "a@example.com").id)
        self.api.claude_usage["at-a"] = claude_usage(100, 30)
        m.refresh()
        self.assertEqual(m.auto_swap(), [self.by_email(m, "c@example.com").id])
        self.assertEqual(self.live_claude().email, "c@example.com")
        self.assertIn(("log", "Claude now uses c@example.com (automatic)"), self.logs)
        m.meta["autoSwap"] = False
        self.api.claude_usage["at-c"] = claude_usage(100, 5)
        m.refresh()
        self.assertEqual(m.auto_swap(), [])

    def test_codex_limit_reached_and_remove(self):
        m = self.manager()
        m.sync_live()
        self.api.codex_usage["at-x"] = codex_usage(97, 50, limit_reached=True)
        m.refresh()
        x = self.by_email(m, "x@example.com")
        self.assertFalse(x.eligible)
        with self.assertRaises(RuntimeError):
            m.remove(x.id)  # cannot remove the account in use

    def test_controller_in_live_mode(self):
        controller = Controller(gateway=lambda notify: LiveGateway(notify, self.vault, self.providers, background=False))
        try:
            controller.gateway.manager.refresh()
            state = controller.snapshot()
            self.assertEqual(state["mode"], "live")
            names = {a["name"] for a in state["accounts"]}
            self.assertEqual(names, {"a@example.com", "x@example.com"})
            claude = next(a for a in state["accounts"] if a["provider"] == "claude")
            self.assertTrue(claude["active"])
            self.assertEqual(claude["headroom"], 60)
            self.assertGreater(claude["renewsAt"], time.time())
            with self.assertRaises(ValueError):
                controller.action("run", {"scenario": "normal"})
        finally:
            controller.close()

    def test_secrets_are_not_in_metadata(self):
        m = self.manager()
        m.sync_live()
        meta = (self.vault.meta_path).read_text()
        self.assertNotIn("rt-a", meta)
        self.assertNotIn("at-x", meta)
        stored = next(self.vault.secret_dir.iterdir()).read_bytes()
        self.assertTrue(stored.startswith(b"PLAIN") or stored.startswith(b"DPAPI"))


if __name__ == "__main__":
    unittest.main()
