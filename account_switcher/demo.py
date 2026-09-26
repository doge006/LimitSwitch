"""Fault-injection Anthropic endpoint. Contains NO live-provider implementation.

The dashboard and installed Claude CLI use this loopback endpoint to make
failure/recovery experiments repeatable without touching subscription accounts.
"""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler

from .local_http import LocalServer
from .core import Router, demo_accounts


class DemoGateway:
    def __init__(self, notify=lambda *_: None):
        self.router = Router(demo_accounts())
        self.notify = notify
        self.lock = threading.RLock()
        self.token = secrets.token_urlsafe(32)
        self.mode = "normal"
        self.injected = False
        self.quota_observed = False
        self.interrupted = False
        self.requests = []
        self.server = None

    def arm(self, mode):
        with self.lock:
            self.mode = mode
            self.injected = self.quota_observed = self.interrupted = False

    def reset(self):
        with self.lock:
            auto = self.router.auto_swap
            self.router = Router(demo_accounts())
            self.router.auto_swap = auto
            self.requests.clear()
            self.arm("normal")

    def prepare_continue(self):
        with self.lock:
            account = self.router.current("claude")
            if not account.eligible:
                account = self.router.fallback("claude")
            if account is None:
                return False
            self.interrupted = self.quota_observed = False
            self.notify("log", f"Continuation routed to {account.alias}.")
            self.notify("accounts", None)
            return True

    def start(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def json_response(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                if status == 429:
                    self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                if self.headers.get("x-api-key") != gateway.token and self.headers.get("Authorization") != "Bearer " + gateway.token:
                    self.json_response(401, {"error": {"type": "authentication_error", "message": "Local session key required"}})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > 2 * 1024 * 1024:
                        self.json_response(413, {"error": "Request too large"})
                        return
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError()
                except (ValueError, TypeError):
                    self.json_response(400, {"error": "Invalid JSON request"})
                    return
                if self.path.split("?")[0] == "/v1/messages/count_tokens":
                    self.json_response(200, {"input_tokens": 10})
                    return
                if self.path.split("?")[0] != "/v1/messages":
                    self.json_response(404, {"error": "Unknown route"})
                    return
                with gateway.lock:
                    account = gateway.router.current("claude")
                    partial = False
                    if not gateway.injected and gateway.mode in ("quota", "partial"):
                        gateway.injected = True
                        gateway.router.exhaust(account)
                        gateway.quota_observed = True
                        gateway.notify("log", f"Injected quota failure on {account.alias}.")
                        if gateway.mode == "quota":
                            selected = gateway.router.fallback("claude")
                            if selected:
                                account = selected
                                gateway.quota_observed = False
                                gateway.notify("log", f"Before-output failover → {account.alias}.")
                        else:
                            gateway.interrupted = partial = True
                    gateway.requests.append({"account": account.id, "stream": bool(body.get("stream")), "partial": partial})
                    gateway.requests[:] = gateway.requests[-100:]
                    gateway.notify("accounts", None)
                    reject = not partial and (gateway.interrupted or not account.eligible)
                    response_text = f"Response completed using {account.alias}. This is a local test response."
                if reject:
                    self.json_response(429, {"type": "error", "error": {"type": "rate_limit_error", "message": "Synthetic account quota exhausted"}})
                    return
                message = {"id": "msg_" + secrets.token_hex(8), "type": "message", "role": "assistant", "model": body.get("model", "claude-sonnet-4-6"), "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}
                if not body.get("stream"):
                    message.update(content=[{"type": "text", "text": response_text}], stop_reason="end_turn")
                    self.json_response(200, message)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def emit(kind, payload):
                    payload["type"] = kind
                    self.wfile.write(("event: " + kind + "\ndata: " + json.dumps(payload) + "\n\n").encode())
                    self.wfile.flush()

                try:
                    emit("message_start", {"message": message})
                    emit("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
                    emit("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Partial response before quota failure…" if partial else response_text}})
                    if partial:
                        emit("error", {"error": {"type": "rate_limit_error", "message": "Synthetic quota exhausted mid-response"}})
                        self.close_connection = True
                        return
                    emit("content_block_stop", {"index": 0})
                    emit("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 10}})
                    emit("message_stop", {})
                except (ConnectionError, OSError):
                    pass

        self.server = LocalServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
