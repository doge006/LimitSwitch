import json
import threading
import time
import unittest
import subprocess
import sys
import shutil
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, build_opener, ProxyHandler

from account_switcher.web import Controller, make_server


class WebTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller(simulator=True)
        self.server = make_server(self.controller, idle_seconds=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        parsed = urlsplit(self.server.launch_url)
        self.base = f"http://{parsed.netloc}"
        self.token = parse_qs(parsed.fragment)["token"][0]
        self.opener = build_opener(ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.controller.close()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, body=None, auth=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + self.token
        if origin:
            headers["Origin"] = origin
        return self.opener.open(Request(self.base + path, json.dumps(body).encode() if body is not None else None, headers), timeout=5)

    def test_assets_state_and_request_protection(self):
        for provider in ('codex', 'claude', 'switcher'):
            with self.request('/assets/' + provider + '.png', auth=False) as response:
                self.assertEqual(response.headers['Content-Type'], 'image/png')
                self.assertTrue(response.read().startswith(b'\x89PNG\r\n\x1a\n'))
        with self.request("/", auth=False) as response:
            self.assertIn(b"Account Switcher", response.read())
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        with self.assertRaises(HTTPError) as unauth:
            self.request("/api/state", auth=False)
        self.assertEqual(unauth.exception.code, 401)
        with self.assertRaises(HTTPError) as cross_origin:
            self.request("/api/reset", {}, origin="https://unrelated.example")
        self.assertEqual(cross_origin.exception.code, 403)
        with self.request("/api/state") as response:
            self.assertEqual(len(json.load(response)["accounts"]), 4)

    def test_manual_swap_and_invalid_input(self):
        with self.request("/api/swap", {"id": "claude-b"}) as response:
            self.assertEqual(response.status, 202)
        self.wait_idle()
        self.assertEqual(self.controller.gateway.router.active["claude"], "claude-b")
        with self.assertRaises(HTTPError) as invalid:
            self.request("/api/preferences", {"afk": "false", "autoSwap": True})
        self.assertEqual(invalid.exception.code, 400)

    def test_taskbar_view_switch_and_display(self):
        self.assertTrue(self.controller.snapshot()["taskbar"])
        with self.request("/api/taskbar", {"on": False}) as response:
            self.assertEqual(response.status, 202)
        with self.request("/api/taskbar", {"display": "right"}):
            pass
        snapshot = self.controller.snapshot()
        self.assertFalse(snapshot["taskbar"])
        self.assertEqual(snapshot["taskbarDisplay"], "right")
        with self.assertRaises(HTTPError) as invalid:
            self.request("/api/taskbar", {"display": 3})
        self.assertEqual(invalid.exception.code, 400)

    def wait_idle(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.controller.snapshot()["busy"]:
                return
            time.sleep(.05)
        self.fail("Web operation did not settle")

    @unittest.skipUnless(shutil.which('claude'), 'Claude CLI required for session recovery')
    def test_web_afk_flow_and_reset_stops_client(self):
        with self.request("/api/preferences", {"afk": True, "autoSwap": True}):
            pass
        self.wait_idle()
        with self.request("/api/run", {"scenario": "partial"}):
            pass
        self.wait_idle()
        snapshot = self.controller.snapshot()
        self.assertEqual(snapshot["status"], "Completed")
        self.assertEqual(self.controller.gateway.router.active["claude"], "claude-b")
        process = self.controller.session.process
        with self.request("/api/reset", {}):
            pass
        self.wait_idle()
        self.assertIsNotNone(process.poll())
        self.assertFalse(self.controller.afk)


class LifecycleTests(unittest.TestCase):
    def test_idle_controller_and_manual_selection_start_no_proxy(self):
        controller = Controller()
        try:
            self.assertIsNone(controller.url)
            self.assertIsNone(controller.gateway.fixture.process)
            controller.action("swap", {"id": "claude-b"})
            deadline = time.monotonic() + 3
            while controller.pending and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(controller.gateway.router.active["claude"], "claude-b")
            self.assertIsNone(controller.gateway.fixture.process)
        finally:
            controller.close()

    def test_unopened_dashboard_exits_after_grace_period(self):
        process = subprocess.Popen([sys.executable, "-m", "account_switcher.web", "--simulator", "--no-browser", "--idle-seconds", "1"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            stdout, stderr = process.communicate(timeout=12)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn("http://127.0.0.1:", stdout)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
