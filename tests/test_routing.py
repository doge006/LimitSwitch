"""Codex router (switch, transparent failover, encrypted items), Codex config edits,
the Claude AFK hook and its decisions. Everything runs against local fakes."""
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
from account_switcher import afk_hook, claude_hooks, codex_config
from account_switcher.codex_proxy import CodexProxy, ThreadState
from account_switcher.integrations import Integrations, RoutedAccounts
from account_switcher.live import LiveAccounts, LiveGateway
from account_switcher.providers import Claude, Codex
from account_switcher.vault import Vault
from account_switcher.web import Controller, make_server
from tests.test_live import FakeAPI, claude_login, claude_usage, codex_login, codex_usage, point_at_fake

LOCAL = build_opener(ProxyHandler({}))


class FakeChatGPT:
    """Responses endpoint. Encrypted items name the token that produced them; another token
    rejects them like the real service does. Tokens in `limited` are out of quota."""

    def __init__(self):
        self.limited, self.seen = set(), []
        self.usage = {}  # token -> primary used percent reported in response headers
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                token = self.headers.get("Authorization", "")[7:]
                upstream.seen.append({"path": self.path, "token": token, "workspace": self.headers.get("ChatGPT-Account-Id"),
                                      "input": body.get("input", [])})
                if token in upstream.limited:
                    return self.send(429, {"error": {"type": "usage_limit_reached", "resets_at": 1999999999}})
                for item in body.get("input", []):
                    owner = str(item.get("encrypted_content", "")).split(":")[0]
                    if item.get("encrypted_content") and owner != token:
                        return self.send(400, {"error": {"code": "invalid_encrypted_content",
                                                         "message": "The encrypted content could not be verified."}})
                items = body.get("input", [])
                n = len(upstream.seen)
                if any(i.get("type") == "compaction_trigger" for i in items):
                    out = [{"type": "compaction", "encrypted_content": f"{token}:cmp{n}"}]
                elif items and "plain-text handoff" in json.dumps(items[-1]):
                    owned = items[0].get("encrypted_content", "")
                    out = [{"type": "message", "role": "assistant",
                            "content": [{"type": "output_text", "text": f"HANDOFF OF {owned}"}]}]
                else:
                    out = [{"type": "reasoning", "summary": [{"type": "summary_text", "text": f"thought {n}"}],
                            "encrypted_content": f"{token}:r{n}"}]
                events = [{"type": "response.output_item.done", "item": item} for item in out]
                events.append({"type": "response.completed", "response": {"id": "r"}})
                raw = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                if token in upstream.usage:
                    self.send_header("x-codex-primary-used-percent", str(upstream.usage[token]))
                    self.send_header("x-codex-primary-window-minutes", "300")
                    self.send_header("x-codex-primary-reset-at", "1999999999")
                self.end_headers()
                self.wfile.write(raw)

            def send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def post(url, body, headers=None):
    request = Request(url, data=json.dumps(body).encode(), method="POST",
                      headers=dict({"Content-Type": "application/json", "Authorization": "Bearer client-token"}, **(headers or {})))
    try:
        with LOCAL.open(request, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.api, self.upstream = FakeAPI(), FakeChatGPT()
        self.claude, self.codex = Claude(home=self.home), Codex(home=self.home)
        point_at_fake(self.claude, self.codex, self.api)
        self.manager = LiveAccounts(lambda *_: None, Vault(root / "store"), {"claude": self.claude, "codex": self.codex})
        self.manager.spacing = 0
        codex_login(self.home, "acct-y", "y@example.com", "at-y", "rt-y")
        self.manager.sync_live()
        codex_login(self.home, "acct-x", "x@example.com", "at-x", "rt-x")  # x is the login in the file
        self.manager.sync_live()
        self.api.codex_usage.update({"at-x": codex_usage(10, 10), "at-y": codex_usage(20, 20)})
        self.manager.refresh(force=True)
        self.x = self.id_of("x@example.com")
        self.y = self.id_of("y@example.com")
        self.manager.enable_routing("codex")
        self.proxy = CodexProxy(RoutedAccounts(self.manager), port=0, upstream=self.upstream.base)
        self.url = self.proxy.start() + "/responses"

    def tearDown(self):
        self.proxy.close()
        self.upstream.close()
        self.api.close()
        self.tmp.cleanup()

    def id_of(self, email):
        return next(a.id for a in self.manager.accounts() if a.email == email)

    def test_uses_the_chosen_account_and_switches_without_touching_files(self):
        status, _ = post(self.url, {"input": []})
        self.assertEqual(status, 200)
        self.assertEqual((self.upstream.seen[-1]["token"], self.upstream.seen[-1]["workspace"]), ("at-x", "acct-x"))
        self.manager.swap(self.y)
        self.assertEqual(self.codex.read_live().email, "x@example.com")  # the file is not rewritten
        post(self.url, {"input": []})
        self.assertEqual((self.upstream.seen[-1]["token"], self.upstream.seen[-1]["workspace"]), ("at-y", "acct-y"))

    def test_usage_limit_is_retried_on_another_account(self):
        self.upstream.limited.add("at-x")
        status, body = post(self.url, {"input": [{"type": "message", "content": "hi"}]})
        self.assertEqual(status, 200)
        self.assertIn(b"response.completed", body)
        self.assertEqual([s["token"] for s in self.upstream.seen], ["at-x", "at-y"])
        self.assertEqual(self.manager.active["codex"], self.y)
        x = next(a for a in self.manager.accounts() if a.id == self.x)
        self.assertFalse(x.eligible)

    def test_no_account_left_passes_the_limit_through(self):
        self.upstream.limited.update({"at-x", "at-y"})
        status, body = post(self.url, {"input": []})
        self.assertEqual(status, 429)
        self.assertIn(b"usage_limit_reached", body)

    def test_auto_swap_off_does_not_move(self):
        self.manager.meta["autoSwap"] = False
        self.upstream.limited.add("at-x")
        status, _ = post(self.url, {"input": []})
        self.assertEqual(status, 429)
        self.assertEqual(self.manager.active["codex"], self.x)

    def test_encrypted_items_follow_the_account_that_made_them(self):
        _, body = post(self.url, {"input": []})
        produced = next(json.loads(line[5:])["item"] for line in body.split(b"\n")
                        if line.startswith(b"data:") and b"reasoning" in line)
        self.manager.swap(self.y)
        history = [{"type": "message", "content": "hi"}, produced, {"type": "message", "content": "go on"}]
        status, _ = post(self.url, {"input": history})
        self.assertEqual(status, 200)
        sent = self.upstream.seen[-1]
        self.assertEqual(sent["token"], "at-y")
        self.assertNotIn("encrypted_content", json.dumps(sent["input"]))  # nothing y cannot read
        self.assertEqual(len(sent["input"]), 3)  # x's reasoning is there, as its summary

    def test_unknown_encrypted_items_are_dropped_after_a_rejection(self):
        foreign = {"type": "reasoning", "encrypted_content": "someone-else:abc"}
        status, _ = post(self.url, {"input": [foreign, {"type": "message", "content": "hi"}]})
        self.assertEqual(status, 200)
        self.assertEqual(len(self.upstream.seen), 2)
        self.assertEqual(self.upstream.seen[-1]["input"], [{"type": "message", "content": "hi"}])

    def test_missing_saved_login_falls_back_to_the_sessions_own(self):
        self.manager.swap(self.y)
        self.manager.vault.delete_secret(self.y)
        post(self.url, {"input": []})
        self.assertEqual(self.upstream.seen[-1]["token"], "client-token")

    def test_websocket_and_wrong_secret(self):
        request = Request(self.url, headers={"Upgrade": "websocket", "Connection": "Upgrade"})
        with self.assertRaises(HTTPError) as caught:
            LOCAL.open(request, timeout=5)
        self.assertEqual(caught.exception.code, 426)
        wrong = self.url.replace(self.proxy.secret, "guess")
        status, _ = post(wrong, {"input": []})
        self.assertEqual(status, 403)
        self.assertEqual(self.upstream.seen, [])

    def items_of(self, body):
        return [json.loads(line[5:])["item"] for line in body.split(b"\n")
                if line.startswith(b"data:") and b"output_item.done" in line]

    def wait_for(self, check, seconds=5):
        deadline = time.time() + seconds
        while time.time() < deadline and not check():
            time.sleep(0.05)
        return check()

    def test_no_extra_requests_and_the_thread_stays_with_its_checkpoints_account(self):
        _, body = post(self.url, {"input": [{"type": "compaction_trigger"}]})
        checkpoint = self.items_of(body)[0]
        self.manager.swap(self.y)
        post(self.url, {"input": [checkpoint, {"type": "message", "role": "user", "content": "go on"}]})
        self.assertEqual(self.upstream.seen[-1]["token"], "at-x")  # x can still read it: stay
        self.assertEqual(self.upstream.seen[-1]["input"][0], checkpoint)
        self.assertEqual(len(self.upstream.seen), 2)  # only Codex's own requests, nothing extra
        self.upstream.limited.add("at-x")  # x used up: the thread moves on without the checkpoint
        status, _ = post(self.url, {"input": [checkpoint, {"type": "message", "role": "user", "content": "go on"}]})
        self.assertEqual((status, self.upstream.seen[-1]["token"]), (200, "at-y"))
        self.assertEqual(self.upstream.seen[-1]["input"], [{"type": "message", "role": "user", "content": "go on"}])

    def test_hidden_reasoning_travels_as_its_summary(self):
        _, body = post(self.url, {"input": []})
        reasoning = self.items_of(body)[0]
        self.manager.swap(self.y)
        post(self.url, {"input": [reasoning, {"type": "message", "role": "user", "content": "go on"}]})
        sent = self.upstream.seen[-1]["input"]
        self.assertEqual(sent[0], {"type": "message", "role": "assistant",
                                   "content": [{"type": "output_text", "text": reasoning["summary"][0]["text"]}]})

    def test_uses_the_account_fully_and_moves_between_turns(self):
        self.upstream.usage["at-x"] = 99
        start = [{"type": "message", "role": "user", "content": "task"}]
        post(self.url, {"input": start})
        post(self.url, {"input": start + [{"type": "message", "role": "user", "content": "next"}]})
        self.assertEqual(self.upstream.seen[-1]["token"], "at-x")  # 99%: keep using it
        self.upstream.usage["at-x"] = 100
        mid_turn = start + [{"type": "function_call", "call_id": "c", "name": "shell", "arguments": "{}"},
                            {"type": "function_call_output", "call_id": "c", "output": "ok"}]
        post(self.url, {"input": mid_turn})  # reports 100%
        post(self.url, {"input": mid_turn + [{"type": "message", "role": "user", "content": "next"}]})
        self.assertEqual(self.upstream.seen[-1]["token"], "at-y")  # used up: the new turn starts on y
        self.assertEqual(self.manager.active["codex"], self.y)

    def test_thread_state_survives_a_restart(self):
        saved = {}
        state = ThreadState(save=saved.update)
        state.remember("enc", "acct")
        state.write_out("enc", "text")
        again = ThreadState(load=lambda: saved)
        self.assertEqual((again.owner("enc"), again.text("enc")), ("acct", "text"))

    def test_quitting_writes_the_chosen_account_into_the_login_file(self):
        self.manager.swap(self.y)
        self.manager.disable_routing("codex")
        self.assertEqual(self.codex.read_live().email, "y@example.com")
        self.assertEqual(self.manager.active["codex"], self.y)

    def test_the_login_files_tokens_are_never_rotated_here(self):
        self.manager.swap(self.y)
        del self.api.codex_usage["at-x"]  # x's token now looks expired to the usage endpoint
        self.manager.refresh(force=True)
        self.assertNotIn(("/codex/token", "rt-x"), self.api.refreshes)  # Codex owns x's tokens

    def test_signing_in_elsewhere_is_followed(self):
        self.manager.swap(self.y)
        self.manager.sync_live()
        self.assertEqual(self.manager.active["codex"], self.y)  # routed choice kept
        codex_login(self.home, "acct-z", "z@example.com", "at-z", "rt-z")
        self.manager.sync_live()
        self.assertEqual(self.manager.active["codex"], self.id_of("z@example.com"))


class CodexConfigTests(unittest.TestCase):
    def test_apply_and_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = ('model = "gpt-6"\nopenai_base_url = "https://example.test/v1"\n\n'
                        '[features]\ndaemon_auto_start = true\nother = true\n\n[projects."C:/work"]\ntrust_level = "trusted"\n')
            path = Path(tmp) / "config.toml"
            path.write_text(original)
            codex_config.apply("http://127.0.0.1:1/s/backend-api/codex", tmp)
            if codex_config.tomllib:
                data = codex_config.tomllib.loads(path.read_text())
                self.assertEqual(data["openai_base_url"], "http://127.0.0.1:1/s/backend-api/codex")
                self.assertEqual(data["features"], {"daemon_auto_start": False, "enable_request_compression": False, "other": True})
                self.assertEqual(data["model"], "gpt-6")
                self.assertEqual(data["projects"]["C:/work"]["trust_level"], "trusted")
            codex_config.apply("http://127.0.0.1:2/s/backend-api/codex", tmp)  # re-applying replaces, never stacks
            self.assertEqual(path.read_text().count("openai_base_url = \"http://127.0.0.1"), 1)
            codex_config.restore(tmp)
            self.assertEqual(path.read_text(), original)

    def test_no_config_yet(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_config.apply("http://127.0.0.1:1/s/backend-api/codex", tmp)
            if codex_config.tomllib:
                data = codex_config.tomllib.loads((Path(tmp) / "config.toml").read_text())
                self.assertFalse(data["features"]["daemon_auto_start"])
            codex_config.restore(tmp)
            self.assertEqual((Path(tmp) / "config.toml").read_text().strip(), "")


class ClaudeHookTests(unittest.TestCase):
    def test_install_keeps_other_settings_and_uninstall_removes_only_ours(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            theirs = {"model": "opus", "hooks": {"StopFailure": [{"hooks": [{"type": "command", "command": "notify.sh"}]}],
                                                 "Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}
            path.write_text(json.dumps(theirs))
            claude_hooks.install(Path(tmp) / "state.json", tmp)
            claude_hooks.install(Path(tmp) / "state.json", tmp)  # idempotent
            data = json.loads(path.read_text())
            ours = [h for g in data["hooks"]["StopFailure"] for h in g["hooks"] if claude_hooks.MARK in h["command"]]
            self.assertEqual(len(ours), 1)
            self.assertTrue(ours[0]["asyncRewake"])
            self.assertEqual(data["model"], "opus")
            claude_hooks.uninstall(tmp)
            self.assertEqual(json.loads(path.read_text()), theirs)

    def test_invalid_settings_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text("{ not json")
            with self.assertRaises(ValueError):
                claude_hooks.install(Path(tmp) / "state.json", tmp)
            self.assertEqual(path.read_text(), "{ not json")


class AfkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.api = FakeAPI()
        claude, codex = Claude(home=self.home), Codex(home=self.home)
        point_at_fake(claude, codex, self.api)
        self.claude = claude
        self.gateway = LiveGateway(lambda *_: None, Vault(root / "store"), {"claude": claude, "codex": codex}, background=False)
        self.manager = self.gateway.manager
        self.manager.spacing = 0
        claude_login(self.home, "uuid-b", "b@example.com", "at-b", "rt-b")
        self.manager.sync_live()
        claude_login(self.home, "uuid-a", "a@example.com", "at-a", "rt-a")
        self.manager.sync_live()
        self.api.claude_usage.update({"at-a": claude_usage(100, 50), "at-b": claude_usage(10, 10)})
        self.manager.refresh(force=True)

    def tearDown(self):
        self.api.close()
        self.tmp.cleanup()

    def test_off_means_leave_the_session_alone(self):
        self.manager.meta["autoSwap"] = False
        self.assertEqual(self.manager.claude_limit("s1"), {"action": "stop"})

    def test_switches_and_continues(self):
        self.gateway.set_afk(True)
        answer = self.manager.claude_limit("s1")
        self.assertEqual(answer["action"], "continue")
        self.assertEqual(self.claude.read_live().email, "b@example.com")  # Claude Code reloads this file

    def test_waits_for_a_reset_when_no_account_has_room(self):
        self.gateway.set_afk(True)
        self.api.claude_usage["at-b"] = claude_usage(100, 10, reset_in=1800)
        self.manager.refresh(force=True)
        answer = self.manager.claude_limit("s1")
        self.assertEqual(answer["action"], "wait")
        self.assertTrue(60 <= answer["seconds"] <= 3700)
        self.api.claude_usage["at-a"] = claude_usage(5, 50)  # the window reset
        self.assertEqual(self.manager.claude_limit("s1")["action"], "continue")

    def test_endpoint_needs_the_hook_token(self):
        self.gateway.set_afk(True)
        controller = Controller(gateway=lambda notify: self.gateway)
        server = make_server(controller, idle_seconds=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            status, _ = post(server.hook_url, {"provider": "claude", "session": "s"})
            self.assertEqual(status, 403)
            status, body = post(server.hook_url, {"provider": "claude", "session": "s"},
                                {"Authorization": "Bearer " + server.hook_token})
            self.assertEqual((status, json.loads(body)["action"]), (200, "continue"))
        finally:
            server.shutdown()
            server.server_close()

    def test_hook_script_wakes_claude_on_continue(self):
        answers = [{"action": "wait", "seconds": 1}, {"action": "continue", "message": "go on"}]

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                raw = json.dumps(answers.pop(0)).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        state = Path(self.tmp.name) / "state.json"
        state.write_text(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/api/afk", "token": "t"}))
        try:
            stderr = io.StringIO()
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"error": "rate_limit", "session_id": "s"}))), \
                    mock.patch.object(sys, "stderr", stderr), mock.patch.object(afk_hook.time, "sleep"):
                self.assertEqual(afk_hook.main(["hook", str(state)]), 2)
            self.assertEqual(stderr.getvalue(), "go on")
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"error": "server_error"}))):
                self.assertEqual(afk_hook.main(["hook", str(state)]), 0)  # other failures: ignored
            state.unlink()
            with mock.patch.object(sys, "stdin", io.StringIO(json.dumps({"error": "rate_limit"}))):
                self.assertEqual(afk_hook.main(["hook", str(state)]), 0)  # app not running
        finally:
            server.shutdown()
            server.server_close()


class ClaudeFullUseTests(AfkTests):
    def test_keeps_using_an_account_until_its_limit(self):
        self.api.claude_usage["at-a"] = claude_usage(99, 50)
        self.manager.refresh(force=True)
        self.assertEqual(self.manager.auto_swap(), [])
        self.assertEqual(self.claude.read_live().email, "a@example.com")

    def test_auto_swap_without_afk_switches_but_does_not_continue(self):
        self.manager.meta["autoSwap"] = True
        self.assertEqual(self.manager.claude_limit("s1"), {"action": "stop"})
        self.assertEqual(self.claude.read_live().email, "b@example.com")  # the next message uses b

    def test_neither_on_leaves_everything_alone(self):
        self.manager.meta["autoSwap"] = False
        self.assertEqual(self.manager.claude_limit("s1"), {"action": "stop"})
        self.assertEqual(self.claude.read_live().email, "a@example.com")


@unittest.skipIf(sys.platform == "win32", "stand-in CLI is a POSIX shell script")
class CodexServerWatchTests(unittest.TestCase):
    def test_stops_the_shared_server_only_when_idle(self):
        from account_switcher.integrations import CodexServerWatch
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "app-server-control").mkdir()
            (home / "app-server-control" / "app-server-control.sock").write_text("")
            day = home / "sessions" / "2026" / "09" / "26"
            day.mkdir(parents=True)
            log = day / "rollout.jsonl"
            log.write_text("{}")
            calls = home / "calls"
            cli = home / "codex"
            cli.write_text(f"#!/bin/sh\necho \"$*\" >> '{calls}'\n")
            cli.chmod(0o755)
            watch = CodexServerWatch(home, cli=str(cli))
            self.assertFalse(watch.check())  # a session is active
            os.utime(log, (time.time() - 600, time.time() - 600))
            self.assertTrue(watch.check())
            self.assertEqual(calls.read_text().split(), ["app-server", "daemon", "stop"])
            self.assertFalse(watch.check())  # the same (stale) socket is not stopped twice


class IntegrationTests(unittest.TestCase):
    def test_start_and_stop_leave_codex_and_claude_as_they_were(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home, claude_root = root / "codex", root / "claude"
            codex_home.mkdir()
            claude_root.mkdir()
            (codex_home / "config.toml").write_text('model = "m"\n')
            (claude_root / "settings.json").write_text('{"model": "opus"}')
            gateway = LiveGateway(lambda *_: None, Vault(root / "store"),
                                  {"claude": Claude(config_dir=claude_root, home=root), "codex": Codex(codex_home=codex_home)},
                                  background=False)
            gateway.manager.meta.update(afk=True, startWithWindows=False)
            integrations = Integrations(gateway, "http://127.0.0.1:1/api/afk", "t", codex_home=codex_home,
                                        claude_root=claude_root, upstream="http://127.0.0.1:9")
            with mock.patch("account_switcher.codex_proxy.DEFAULT_PORT", 0), \
                    mock.patch("account_switcher.integrations.DEFAULT_PORT", 0):
                integrations.start()
            try:
                self.assertIn("openai_base_url", (codex_home / "config.toml").read_text())
                self.assertIn(claude_hooks.MARK, (claude_root / "settings.json").read_text())
                self.assertTrue(integrations.state_file.exists())
            finally:
                integrations.stop()
            self.assertEqual((codex_home / "config.toml").read_text(), 'model = "m"\n')
            self.assertEqual(json.loads((claude_root / "settings.json").read_text()), {"model": "opus"})
            self.assertFalse(integrations.state_file.exists())


if __name__ == "__main__":
    unittest.main()
