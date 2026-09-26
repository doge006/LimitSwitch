"""On-demand loopback Web UI. Python standard library; no npm or hosted assets."""
import argparse
from collections import deque
from dataclasses import asdict
import json
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import webbrowser

from .client import ClaudeSession
from .core import CONTINUE
from .demo import DemoGateway

ASSETS = Path(__file__).with_name("static")


class Controller:
    def __init__(self, simulator=False, live=False, gateway=None):
        self.condition = threading.Condition(threading.RLock())
        self.operations = threading.Lock()
        self.revision = 0
        self.log = deque(maxlen=150)
        self.log_total = 0
        self.output = ""
        self.status = "Ready"
        self.pending = False
        self.afk = False
        self.closed = False
        self.session = None
        self.simulator = simulator
        if gateway is not None:
            self.gateway = gateway(self.notify)
        elif live:
            from .live import LiveGateway
            self.gateway = LiveGateway(self.notify)
        elif simulator:
            self.gateway = DemoGateway(self.notify)
        else:
            from .proxy_demo import ProxyDemoGateway
            self.gateway = ProxyDemoGateway(self.notify)
        self.live = getattr(self.gateway, "live", False)
        # Listing accounts and keeping a tray icon available should not start
        # the Go proxy or an official client. Start inference only on Run.
        self.url = None
        # Account ids the user picked by hand (dashboard, tray menu or reset), so the
        # tray can tell those apart from automatic failovers worth a notification.
        self.manual_swaps = set()

    def notify(self, kind, value):
        with self.condition:
            if kind == "log":
                self.log_total += 1
                self.log.append({"id": self.log_total, "at": time.time(), "text": str(value)})
            elif kind == "text":
                self.output = (self.output + str(value))[-24000:]
            elif kind == "state":
                self.status = value
            elif kind == "result":
                self.log_total += 1
                self.log.append({"id": self.log_total, "at": time.time(),
                                 "text": f"Turn {'failed' if value['failed'] else 'completed'} · session {str(value['session_id'])[:8]}"})
                if value["failed"]:
                    self.output = (self.output + "\n\n[Response interrupted. Waiting for recovery.]\n\n")[-24000:]
            self.revision += 1
            self.condition.notify_all()

    def snapshot(self):
        # Read accounts before taking the controller lock: the live account manager has its
        # own lock and posts log lines (which need this lock) while holding it.
        router = self.gateway.router
        active = dict(router.active)
        accounts = [account_view(a, active.get(a.provider) == a.id) for a in router.accounts]
        auto_swap = router.auto_swap
        with self.condition:
            return {
                "revision": self.revision,
                "mode": "live" if self.live else "demo",
                "accounts": accounts,
                "autoSwap": auto_swap, "afk": self.afk,
                "busy": self.pending or bool(self.session and (self.session.busy or self.session.recovering)),
                "status": self.status, "output": self.output, "log": list(self.log),
                "backend": "Real accounts" if self.live else "Routing simulator" if self.simulator else "Compiled proxy fork" if self.url else "Proxy starts on demand",
                "signingIn": sorted(getattr(getattr(self.gateway, "manager", None), "logins", {})),
                "sessionId": self.session.session_id if self.session else None,
                "clientPid": self.session.process.pid if self.session and self.session.process else None,
            }

    def action(self, action, body):
        if self.closed:
            raise RuntimeError("The server is shutting down")
        if action not in {"preferences", "swap", "reset", "run", "continue", "stop", "refresh", "add", "remove", "subscription"}:
            raise ValueError("Unknown action")
        if action == "refresh":  # cheap and lock-free: just nudges the usage refresher
            if hasattr(self.gateway, "poke"):
                self.gateway.poke(body.get("ifOlderThan", 0) if isinstance(body.get("ifOlderThan", 0), (int, float)) else 0)
            return
        if action == "subscription":  # manual renewal / end date; instant, no worker thread
            if not self.live:
                raise ValueError("Renewal dates need real-account mode")
            at = body.get("at")
            if at is not None and (not isinstance(at, (int, float)) or isinstance(at, bool)):
                raise ValueError("Date must be a timestamp or null")
            self.gateway.manager.set_subscription(body.get("id"), at, bool(body.get("ends")))
            return
        if action in {"add", "remove"} and not self.live:
            raise ValueError("Adding and removing accounts needs real-account mode")
        if action == "add" and body.get("provider") not in {"claude", "codex"}:
            raise ValueError("Choose Claude or Codex")
        if action in {"run", "continue"} and self.live:
            raise ValueError("The Recovery lab runs in demo mode only (--demo)")
        if action == "run" and body.get("scenario") not in {"normal", "quota", "partial"}:
            raise ValueError("Choose a supported test scenario")
        if action in {"swap", "remove"} and body.get("id") not in {a.id for a in self.gateway.router.accounts}:
            raise ValueError("Unknown account")
        if action == "preferences" and (type(body.get("afk")) is not bool or type(body.get("autoSwap")) is not bool):
            raise ValueError("Preferences must be booleans")
        if not self.operations.acquire(blocking=False):
            raise RuntimeError("An operation is already running")
        self.pending = True
        self.notify("changed", None)

        def work():
            try:
                if self.session and (self.session.busy or self.session.recovering) and action not in {"stop"}:
                    raise RuntimeError("Wait for the current turn to settle or stop it")
                if action == "preferences":
                    self.afk = body["afk"]
                    self.gateway.router.auto_swap = body["autoSwap"]
                    if self.session:
                        self.session.recovery.enable(self.afk)
                    if hasattr(self.gateway, "apply_preferences"):
                        self.gateway.apply_preferences()
                elif action == "swap":
                    self.manual_swaps.add(body["id"])
                    account = self.gateway.swap(body["id"]) if hasattr(self.gateway, "swap") else self.gateway.router.swap(body["id"])
                    if not self.live:
                        self.notify("log", f"Next {account.provider} request selected: {account.alias}")
                elif action == "add":
                    self.gateway.manager.add(body["provider"])
                elif action == "remove":
                    self.gateway.manager.remove(body["id"])
                elif action == "reset" and self.live:
                    self.gateway.reset()  # the refresh button: fetch usage now
                elif action in {"stop", "reset"}:
                    self.stop_session()
                    if action == "reset":
                        self.gateway.reset()
                        self.manual_swaps.update(self.gateway.router.active.values())
                        self.output = ""
                        self.notify("log", "Synthetic accounts reset; AFK is off.")
                elif action == "run":
                    if self.url is None:
                        try:
                            self.url = self.gateway.start()
                        except Exception:
                            self.gateway.close()
                            raise
                    if not self.session or not self.session.process or self.session.process.poll() is not None:
                        if self.session:
                            self.session.close()
                        self.session = ClaudeSession(self.gateway, self.notify)
                        self.session.start(self.url)
                    self.gateway.arm(body["scenario"])
                    self.session.recovery.enable(self.afk)
                    self.output = ""
                    self.session.send("Say hello briefly.")
                elif action == "continue":
                    if not self.session:
                        raise RuntimeError("Start a test session first")
                    if not self.gateway.prepare_continue():
                        raise RuntimeError("Select an available account or enable Auto swap")
                    self.session.send(CONTINUE)
            except Exception as error:
                self.notify("log", str(error))
                self.notify("state", "Needs attention")
            finally:
                self.pending = False
                self.operations.release()
                self.notify("changed", None)

        threading.Thread(target=work, daemon=True).start()

    def stop_session(self):
        if self.session:
            self.session.close()
            self.session = None
        self.afk = False
        # Release the expensive inference process as soon as the user stops.
        if self.url:
            self.gateway.close()
            self.url = None
        self.notify("state", "Stopped")

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        with self.operations:
            self.stop_session()
            self.gateway.close()


def account_view(account, active):
    """JSON shape the dashboard and tray use for one account."""
    view = asdict(account)
    view.pop("usage", None)
    view.update(active=active, eligible=account.eligible, windows=account.windows(),
                headroom=account.headroom, renewsAt=account.renews_at,
                name=account.email or account.alias.split(" · ")[-1].replace(" (synthetic)", ""))
    return view


def make_server(controller, port=0, idle_seconds=90):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, status, body, mime="application/json"):
            raw = json.dumps(body).encode() if mime == "application/json" else body
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            try:
                self.end_headers()
                self.wfile.write(raw)
            except ConnectionError:
                pass

        def authorized(self):
            if self.headers.get("Host") != self.server.expected_host:
                self.respond(403, {"error": "Invalid local host"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.server.expected_host:
                self.respond(403, {"error": "Cross-origin request rejected"})
                return False
            if not secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                self.respond(401, {"error": "Open the private launch URL to connect"})
                return False
            self.server.last_seen = time.monotonic()
            return True

        def do_GET(self):
            if self.headers.get("Host") != self.server.expected_host:
                self.respond(403, {"error": "Invalid local host"})
                return
            path = self.path.split("?")[0]
            if path in ("/assets/codex.png", "/assets/claude.png", "/assets/switcher.png"):
                self.respond(200, (ASSETS / path.lstrip("/")).read_bytes(), "image/png")
                return
            assets = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in assets:
                name, mime = assets[path]
                self.respond(200, (ASSETS / name).read_bytes(), mime)
                return
            if path != "/api/state" or not self.authorized():
                if path != "/api/state":
                    self.respond(404, {"error": "Unknown route"})
                return
            from urllib.parse import parse_qs, urlsplit
            try:
                after = int(parse_qs(urlsplit(self.path).query).get("after", ["-1"])[0])
            except ValueError:
                self.respond(400, {"error": "Invalid revision"})
                return
            with controller.condition:
                controller.condition.wait_for(lambda: controller.revision != after or controller.closed, timeout=20)
            self.respond(200, controller.snapshot())

        def do_POST(self):
            if not self.authorized():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 <= size <= 4096:
                    raise ValueError("Request too large")
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                if self.path == "/api/shutdown":
                    self.respond(200, {"ok": True})
                    # A host (the tray) can supply its own quit; otherwise stop serving.
                    threading.Thread(target=getattr(self.server, "quit", self.server.shutdown), daemon=True).start()
                    return
                if not self.path.startswith("/api/"):
                    raise ValueError("Unknown route")
                controller.action(self.path[5:], body)
                self.respond(202, {"ok": True})
            except ValueError as error:
                self.respond(400, {"error": str(error)})
            except RuntimeError as error:
                self.respond(409, {"error": str(error)})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.expected_host = f"127.0.0.1:{server.server_port}"
    server.last_seen = time.monotonic()
    server.launch_url = f"http://{server.expected_host}/#token={token}"

    def idle_watch():
        while not controller.closed:
            time.sleep(5)
            if time.monotonic() - server.last_seen > idle_seconds:
                server.shutdown()
                return
    # Only the standalone dev server exits when unused; the tray host passes 0 and
    # so gets no watcher thread (no periodic wake-ups at all).
    if idle_seconds:
        threading.Thread(target=idle_watch, daemon=True).start()
    return server


def write_url_file(path, url):
    """Record the tokenised launch URL so a local script can request a clean shutdown."""
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(url, encoding="utf-8")


def clear_url_file(path, url):
    """Remove the launch URL on exit, unless a newer instance has replaced it."""
    try:
        target = Path(path) if path else None
        if target and target.read_text(encoding="utf-8") == url:
            target.unlink()
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Synthetic accounts and the Recovery lab instead of your real logins")
    parser.add_argument("--simulator", action="store_true", help="Demo mode using a lightweight routing simulation instead of the compiled proxy")
    parser.add_argument("--idle-seconds", type=int, default=90)
    parser.add_argument("--url-file", help="Write the private launch URL here (used by the update script)")
    args = parser.parse_args()
    controller = Controller(args.simulator, live=not (args.demo or args.simulator))
    server = make_server(controller, args.port, args.idle_seconds)
    write_url_file(args.url_file, server.launch_url)
    print(server.launch_url, flush=True)
    if not args.no_browser:
        webbrowser.open(server.launch_url)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        controller.close()
        server.server_close()
        clear_url_file(args.url_file, server.launch_url)


if __name__ == "__main__":
    main()
