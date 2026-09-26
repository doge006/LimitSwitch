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


def point_at_fake(claude, codex, api):
    """Never talk to the real providers from tests."""
    claude.USAGE_URL, claude.TOKEN_URL, claude.PROFILE_URL = api.base + "/claude/usage", api.base + "/claude/token", api.base + "/claude/profile"
    codex.USAGE_URL, codex.TOKEN_URL, codex.CHECK_URL = api.base + "/codex/usage", api.base + "/codex/token", api.base + "/codex/check"


class FakeAPI:
    """Usage per access token; token endpoints rotate tokens and count calls."""

    def __init__(self):
        self.claude_usage, self.codex_usage = {}, {}
        self.claude_profile, self.codex_check = {}, {}
        self.calls = []
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
                api.calls.append(self.path)
                if self.path in ("/claude/profile", "/codex/check"):
                    table = api.claude_profile if self.path == "/claude/profile" else api.codex_check
                    return self.reply(200, table.get(token, {"account": {"email": "x"}}))
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
        point_at_fake(claude, codex, self.api)
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
        m = LiveAccounts(lambda kind, value: self.logs.append((kind, value)), self.vault, self.providers)
        m.spacing = 0
        return m

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
        m.refresh(force=True)
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
        m.refresh(force=True)
        self.assertEqual(m.auto_swap(), [self.by_email(m, "c@example.com").id])
        self.assertEqual(self.live_claude().email, "c@example.com")
        self.assertIn(("log", "Claude now uses c@example.com (automatic)"), self.logs)
        m.meta["autoSwap"] = False
        self.api.claude_usage["at-c"] = claude_usage(100, 5)
        m.refresh(force=True)
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
            controller.gateway.manager.spacing = 0
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

    def test_schedule_spares_the_api(self):
        m = self.manager()
        m.sync_live()
        claude_login(self.home, "uuid-b", "b@example.com", "at-b", "rt-b", expires_in=36000)
        self.api.claude_usage["at-b"] = claude_usage(10, 10, reset_in=7200)
        m.sync_live()
        m.swap(self.by_email(m, "a@example.com").id)
        m.refresh()
        usage_calls = lambda: [c for c in self.api.calls if c.endswith("/usage")]
        first = len(usage_calls())
        m.refresh()  # nothing is due yet: no requests at all
        self.assertEqual(len(usage_calls()), first)
        b_id = self.by_email(m, "b@example.com").id
        a_id = m.active["claude"]
        now = time.time()
        self.assertAlmostEqual(m.due(a_id, m.meta["accounts"][a_id], True, now) - now, 300, delta=5)
        self.assertGreater(m.due(b_id, m.meta["accounts"][b_id], False, now) - now, 1700)  # inactive: 30 min
        # A passed reset is applied locally without asking the API.
        m.meta["accounts"][b_id]["usage"][0]["resetsAt"] = now - 5
        b = self.by_email(m, "b@example.com")
        self.assertEqual(b.windows()[0]["used"], 0.0)
        self.assertEqual(len(usage_calls()), first)
        # Opening the panel only refetches stale data; a 429 backs off.
        m.refresh(max_age=120)
        self.assertEqual(len(usage_calls()), first)
        self.api.claude_usage.pop("at-a")
        m.meta["accounts"][a_id]["updatedAt"] = 0

    def test_failing_account_is_not_retried_in_a_loop(self):
        m = self.manager()
        m.sync_live()
        self.api.claude_usage.pop("at-a")  # every fetch for A now fails (401 on the live login)
        m.refresh(force=True)
        a = self.by_email(m, "a@example.com")
        self.assertIn("Waiting for Claude", a.status)
        now = time.time()
        self.assertGreater(m.due(a.id, m.meta["accounts"][a.id], True, now) - now, 250)
        self.assertGreaterEqual(m.next_delay(), 20)

    def test_backoff_on_rate_limit(self):
        from account_switcher.providers import ProviderError
        m = self.manager()
        m.sync_live()
        calls = []
        def limited(secret, allow_refresh):
            calls.append(1)
            raise ProviderError("Usage API is rate limiting; will retry", retry_after=60)
        self.providers["claude"].fetch = limited
        m.refresh(force=True)
        m.refresh(force=True)
        self.assertEqual(len(calls), 1)  # second call held back by the backoff
        a = self.by_email(m, "a@example.com")
        until, failures = m.backoff[a.id]
        self.assertGreaterEqual(until - time.monotonic(), 290)  # at least 5 minutes, not just Retry-After
        self.assertIn("rate limiting", a.status)

    def test_subscription_dates_credits_and_manual_override(self):
        m = self.manager()
        m.sync_live()
        renew = time.time() + 12 * 86400
        self.api.claude_profile["at-a"] = {"organization": {"subscription": {"current_period_end": renew, "cancel_at_period_end": True}}}
        self.api.claude_usage["at-a"] = dict(claude_usage(10, 10), extra_usage={"is_enabled": True, "monthly_limit": 5000, "used_credits": 1250, "utilization": 25})
        self.api.codex_usage["at-x"] = dict(codex_usage(5, 5), credits={"has_credits": True, "unlimited": False, "balance": "12.5"})
        m.refresh(force=True)
        a, x = self.by_email(m, "a@example.com"), self.by_email(m, "x@example.com")
        self.assertEqual(a.subscription["ends"], True)
        self.assertAlmostEqual(a.subscription["at"], renew, delta=1)
        self.assertEqual(a.credits, {"kind": "extra", "enabled": True, "limit": 5000.0, "used": 1250.0, "utilization": 25.0})
        self.assertEqual(x.credits["balance"], 12.5)
        fields = json.loads((self.vault.root / "subscription-fields.json").read_text())
        self.assertIn("organization.subscription.current_period_end", fields["claude"])
        self.assertNotIn(str(int(renew)), json.dumps(fields))  # names only, never values
        m.set_subscription(x.id, renew + 86400, ends=False)
        self.assertEqual(self.by_email(m, "x@example.com").subscription, {"at": renew + 86400, "ends": False, "source": "manual"})
        profile_calls = len([c for c in self.api.calls if c == "/claude/profile"])
        m.refresh(force=True)
        self.assertEqual(len([c for c in self.api.calls if c == "/claude/profile"]), profile_calls)  # once a day


if __name__ == "__main__":
    unittest.main()
