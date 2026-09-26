"""Local router for Codex: every Codex session (CLI, IDE extension) sends its model requests
here, and they are forwarded to ChatGPT with the login of the account selected in the app.

Why a router rather than rewriting ~/.codex/auth.json: a running Codex keeps its login in
memory, so a file switch only reaches new sessions. Here a switch applies to the very next
request of every session, and when an account hits its usage limit the same request is
retried on another account, so a session never sees the error (Auto swap / AFK).

Details handled here:
- The request path carries a random secret, so other local programs cannot borrow the logins.
- Codex's Responses-over-WebSocket attempt gets 426, which makes it use plain HTTP at once.
- Codex replays encrypted reasoning/compaction items that only the account that produced them
  can read. Items produced by one account are dropped before a request goes to another, and
  if the provider still rejects encrypted content the request is retried once without the
  items this router has not seen.
Nothing is logged or stored except a bounded, in-memory map of encrypted-item fingerprints.
"""
from collections import OrderedDict
from hashlib import blake2b
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

UPSTREAM = "https://chatgpt.com"
DEFAULT_PORT = 47821
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "host", "content-length", "authorization", "chatgpt-account-id",
              "accept-encoding"}
MAX_BODY = 64 * 1024 * 1024
FINGERPRINTS = 20000
TIMEOUT = 600


def fingerprint(value):
    return blake2b(value.encode() if isinstance(value, str) else value, digest_size=12).digest()


def encrypted_values(node):
    """Every encrypted_content string anywhere inside a JSON value."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "encrypted_content" and isinstance(value, str) and value:
                yield value
            else:
                yield from encrypted_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from encrypted_values(value)


def usage_limit(status, body):
    """(True, resets_at) when the provider says this account is out of quota."""
    if status != 429:
        return False, None
    try:
        error = json.loads(body).get("error") or {}
    except (ValueError, AttributeError):
        return False, None
    if error.get("type") not in ("usage_limit_reached", "usage_not_included"):
        return False, None
    resets = error.get("resets_at")
    if resets is None and isinstance(error.get("resets_in_seconds"), (int, float)):
        resets = time.time() + error["resets_in_seconds"]
    return True, resets if isinstance(resets, (int, float)) else None


def encrypted_rejected(status, body):
    if status != 400:
        return False
    text = body[:4000].decode("utf-8", "replace").lower()
    return "encrypted" in text and ("content" in text or "organization" in text)


class CodexRouter:
    """The routing logic, independent of HTTP so it can be tested directly.

    accounts: object with
      route()                    -> account id to use now, or None to pass requests through
      credentials(account_id)    -> (access_token, chatgpt_account_id)
      limit_hit(account_id, resets_at) -> another account id to retry on, or None
    """

    def __init__(self, accounts):
        self.accounts = accounts
        self.seen = OrderedDict()     # fingerprint -> account id that produced it
        self.lock = threading.Lock()

    def record(self, account_id, payload):
        """Remember which account produced the encrypted items in a response chunk."""
        try:
            data = json.loads(payload)
        except ValueError:
            return
        with self.lock:
            for value in encrypted_values(data):
                key = fingerprint(value)
                self.seen[key] = account_id
                self.seen.move_to_end(key)
            while len(self.seen) > FINGERPRINTS:
                self.seen.popitem(last=False)

    def prepare(self, body, account_id, drop_unknown=False):
        """Request body for account_id, without encrypted items it cannot read."""
        if not body or b'"encrypted_content"' not in body:
            return body
        try:
            data = json.loads(body)
        except ValueError:
            return body
        items = data.get("input") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return body
        kept = []
        with self.lock:
            for item in items:
                values = list(encrypted_values(item))
                owners = {self.seen.get(fingerprint(v)) for v in values}
                foreign = any(owner not in (None, account_id) for owner in owners)
                unknown = None in owners
                if values and (foreign or (drop_unknown and unknown)):
                    continue
                kept.append(item)
        if len(kept) == len(items):
            return body
        data["input"] = kept
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()


class CodexProxy:
    def __init__(self, accounts, port=DEFAULT_PORT, upstream=UPSTREAM, secret=None):
        self.router = CodexRouter(accounts)
        self.accounts = accounts
        self.port = port
        self.upstream = upstream.rstrip("/")
        self.secret = secret or secrets.token_urlsafe(18)
        self.server = None
        self.opener = build_opener(ProxyHandler())  # honours the system proxy settings

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/{self.secret}/backend-api/codex"

    def start(self):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_GET(self):
                proxy.handle(self)

            do_POST = do_PUT = do_PATCH = do_DELETE = do_GET

        for port in [self.port] + list(range(self.port + 1, self.port + 20)):
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
                break
            except OSError:
                continue
        else:
            raise RuntimeError("No free local port for the Codex router")
        self.server.daemon_threads = True
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True, name="codex-router").start()
        return self.base_url

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    # ---------- one request ----------
    def handle(self, h):
        prefix = "/" + self.secret + "/"
        if not h.path.startswith(prefix):
            return self.reply(h, 403, b'{"error":{"message":"Unknown route"}}')
        path = h.path[len(prefix) - 1:]
        if h.headers.get("Upgrade", "").lower() == "websocket":
            # Codex falls back to HTTP for the session as soon as it sees 426.
            return self.reply(h, 426, b'{"error":{"message":"Use HTTP"}}')
        try:
            body = self.read_body(h)
        except ValueError as error:
            return self.reply(h, 413, json.dumps({"error": {"message": str(error)}}).encode())
        headers = {k: v for k, v in h.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["Accept-Encoding"] = "identity"
        own = {k: v for k, v in h.headers.items() if k.lower() in ("authorization", "chatgpt-account-id")}
        account = self.accounts.route()
        if account is None:
            headers.update(own)  # not routing: pass the session's login through unchanged
        tried, drop_unknown = set(), False
        while True:
            outgoing = dict(headers)
            payload = body
            if account is not None:
                try:
                    token, workspace = self.accounts.credentials(account)
                except Exception:
                    token, workspace = None, None
                if token:
                    outgoing["Authorization"] = "Bearer " + token
                    if workspace:
                        outgoing["ChatGPT-Account-Id"] = workspace
                else:  # no usable saved login: send the session's own rather than none
                    outgoing.update(own)
                payload = self.router.prepare(body, account, drop_unknown)
            try:
                response = self.opener.open(Request(self.upstream + path, data=payload if h.command != "GET" else None,
                                                    headers=outgoing, method=h.command), timeout=TIMEOUT)
            except HTTPError as error:
                response = error
            except (URLError, OSError) as error:
                return self.reply(h, 502, json.dumps({"error": {"message": f"Could not reach ChatGPT: {error}"}}).encode())
            status = response.status if hasattr(response, "status") else response.code
            if status in (400, 429) and account is not None:
                data = response.read()
                limited, resets = usage_limit(status, data)
                if limited:
                    tried.add(account)
                    following = self.accounts.limit_hit(account, resets)
                    if following is not None and following not in tried:
                        account, drop_unknown = following, False
                        continue
                elif encrypted_rejected(status, data) and not drop_unknown:
                    drop_unknown = True
                    continue
                return self.reply(h, status, data, response.headers)
            return self.stream(h, response, status, account)

    def read_body(self, h):
        if h.headers.get("Transfer-Encoding", "").lower() == "chunked":
            parts, size = [], 0
            while True:
                length = int(h.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if length == 0:
                    h.rfile.readline()
                    break
                size += length
                if size > MAX_BODY:
                    raise ValueError("Request too large")
                parts.append(h.rfile.read(length))
                h.rfile.readline()
            return b"".join(parts)
        length = int(h.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("Request too large")
        return h.rfile.read(length) if length else b""

    def reply(self, h, status, data, headers=None):
        h.send_response(status)
        for key, value in (headers.items() if headers else []):
            if key.lower() not in HOP_BY_HOP and key.lower() != "content-encoding":
                h.send_header(key, value)
        if not headers:
            h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def stream(self, h, response, status, account):
        """Pass the response through as it arrives, noting who produced encrypted items."""
        h.send_response(status)
        for key, value in response.headers.items():
            if key.lower() not in HOP_BY_HOP:
                h.send_header(key, value)
        h.send_header("Connection", "close")
        h.end_headers()
        h.close_connection = True
        pending = b""
        watch = account is not None
        try:
            while True:
                chunk = response.read1(65536) if hasattr(response, "read1") else response.read(65536)
                if not chunk:
                    break
                h.wfile.write(chunk)
                h.wfile.flush()
                if watch:
                    pending += chunk
                    *lines, pending = pending.split(b"\n")
                    for line in lines:
                        if b'"encrypted_content"' in line:
                            self.router.record(account, line[5:] if line.startswith(b"data:") else line)
                    if len(pending) > MAX_BODY:
                        pending = b""
            if watch and pending and b'"encrypted_content"' in pending:
                self.router.record(account, pending[5:] if pending.startswith(b"data:") else pending)
        except (ConnectionError, OSError):
            pass  # the client went away; stop reading
        finally:
            response.close()
