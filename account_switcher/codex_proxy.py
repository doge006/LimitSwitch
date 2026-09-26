"""Local router for Codex: every Codex session sends its model requests here, and they are
forwarded to ChatGPT with the login of the account selected in the app.

A switch applies to the very next request of every open session, and a request that hits a
usage limit is sent again on another account, so the session never sees the error.

Across accounts. Codex replays items the service encrypted for the account that produced
them; another account cannot read them. No extra requests are made for this:
- Hidden reasoning is replaced by the plain-text summary the service returned with it.
- A compaction checkpoint (the summary Codex keeps instead of old history) keeps its thread
  on the checkpoint's account while that account can take requests; once it is used up the
  thread moves on without the checkpoint (the visible recent conversation is kept).
- A used-up account is left when a new turn starts, from the service's usage headers.
The request path carries a random secret, so other local programs cannot borrow the logins.
Codex's WebSocket attempt gets 426, which makes it use plain HTTP at once.
Nothing is logged. Fingerprints of encrypted items (which account made which) are kept, in
the app's encrypted store, so threads keep working across app restarts.
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
MAX_OWNERS = 50000
MAX_TEXTS = 300
TIMEOUT = 600
CHECKPOINTS = ("compaction", "compaction_summary", "context_compaction")
# Codex's own wording for a plain-text checkpoint (codex-rs/prompts/templates/compact/summary_prefix.md).
SUMMARY_PREFIX = ("Another language model started to solve this problem and produced a summary of its thinking "
                  "process. You also have access to the state of the tools that were used by that language model. "
                  "Use this to build on the work that has already been done and avoid duplicating work. Here is the "
                  "summary produced by the other language model, use the information in this summary to assist with "
                  "your own analysis:")

def fingerprint(value):
    return blake2b(value.encode() if isinstance(value, str) else value, digest_size=12).hexdigest()


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


def usage_headers(headers):
    """Live usage from the service's response headers: [(window minutes, used %, reset time)]."""
    windows = []
    for name in ("primary", "secondary"):
        used = headers.get(f"x-codex-{name}-used-percent")
        minutes = headers.get(f"x-codex-{name}-window-minutes")
        if used is None or minutes is None:
            continue
        try:
            reset = headers.get(f"x-codex-{name}-reset-at")
            windows.append((int(float(minutes)), float(used), float(reset) if reset else None))
        except ValueError:
            continue
    return windows


def new_turn(items):
    """A request that starts a turn ends with the user's message (not a tool result)."""
    last = items[-1] if items else None
    return isinstance(last, dict) and last.get("type", "message") == "message" and last.get("role") == "user"


def checkpoint_message(text):
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"{SUMMARY_PREFIX}\n{text}"}]}


def reasoning_message(item):
    summary = " ".join(part.get("text", "") for part in item.get("summary") or [] if isinstance(part, dict)).strip()
    if not summary:
        return None
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": summary}]}


class ThreadState:
    """Who produced each encrypted item, and plain-text versions of checkpoints."""

    def __init__(self, load=None, save=None):
        self.lock = threading.Lock()
        self.owners, self.texts = OrderedDict(), OrderedDict()
        self.save_hook = save
        self.dirty = False
        if load:
            try:
                data = load() or {}
                self.owners.update(data.get("owners") or {})
                self.texts.update(data.get("texts") or {})
            except Exception:
                pass

    def owner(self, value):
        with self.lock:
            return self.owners.get(fingerprint(value))

    def text(self, value):
        with self.lock:
            return self.texts.get(fingerprint(value))

    def remember(self, value, account_id):
        key = fingerprint(value)
        with self.lock:
            if self.owners.get(key) != account_id:
                self.owners[key] = account_id
                self.dirty = True
            self.owners.move_to_end(key)
            while len(self.owners) > MAX_OWNERS:
                self.owners.popitem(last=False)

    def write_out(self, value, text):
        with self.lock:
            self.texts[fingerprint(value)] = text
            while len(self.texts) > MAX_TEXTS:
                self.texts.popitem(last=False)
            self.dirty = True
        self.flush()

    def flush(self):
        if not (self.dirty and self.save_hook):
            return
        with self.lock:
            snapshot = {"owners": dict(self.owners), "texts": dict(self.texts)}
            self.dirty = False
        try:
            self.save_hook(snapshot)
        except Exception:
            pass


class CodexRouter:
    """Routing decisions, independent of HTTP so they can be tested directly.

    accounts: object with
      route()                       -> account id in use, or None to pass requests through
      credentials(account_id)       -> (access_token, chatgpt_account_id)
      limit_hit(account_id, resets) -> another account id to retry on, or None
      turn_start(account_id)        -> account to use for a new turn (a planned switch)
      usable(account_id)            -> whether this account can take a request now
      observe(account_id, windows)  -> live usage from response headers
    """

    def __init__(self, accounts, state=None):
        self.accounts = accounts
        self.state = state or ThreadState()

    def record(self, account_id, data, on_checkpoint=None):
        """Note which account produced the encrypted items in a response event."""
        items = []
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if isinstance(node.get("encrypted_content"), str) and node["encrypted_content"]:
                    items.append(node)
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
            elif isinstance(node, list):
                stack.extend(node)
        for item in items:
            self.state.remember(item["encrypted_content"], account_id)
            if item.get("type") in CHECKPOINTS and on_checkpoint and self.state.text(item["encrypted_content"]) is None:
                on_checkpoint(item)

    def prepare(self, data, target, drop_unknown=False, exclude=()):
        """(input for `target`, account the request must go to instead or None).

        Items `target` cannot read are replaced by their plain-text versions. A checkpoint
        without one yet keeps the request on the checkpoint's own account while it can
        take requests, so nothing is lost."""
        items = data.get("input") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return data, None
        prepared, changed = [], False
        for item in items:
            values = list(encrypted_values(item)) if isinstance(item, dict) else []
            if not values:
                prepared.append(item)
                continue
            owners = {self.state.owner(v) for v in values}
            foreign = [o for o in owners if o not in (None, target)]
            if not foreign and not (drop_unknown and None in owners):
                prepared.append(item)
                continue
            kind = item.get("type")
            if kind in CHECKPOINTS:
                text = self.state.text(values[0])
                if text is None:
                    owner = foreign[0] if foreign else None
                    if owner is not None and owner not in exclude and self.accounts.usable(owner):
                        return data, owner  # stay with the account that can read it
                    changed = True  # nobody left who can read it: continue without it
                    continue
                prepared.append(checkpoint_message(text))
            elif kind == "reasoning":
                replacement = reasoning_message(item)
                if replacement:
                    prepared.append(replacement)
            changed = True
        if not changed:
            return data, None
        return dict(data, input=prepared), None


class CodexProxy:
    def __init__(self, accounts, port=DEFAULT_PORT, upstream=UPSTREAM, secret=None, state=None):
        self.router = CodexRouter(accounts, state)
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
        self.router.state.flush()
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
        if account is None:  # not routing: pass the session's login through unchanged
            return self.forward(h, path, dict(headers, **own), body, None, None)
        data = None
        if body and body.lstrip()[:1] == b"{" and (b'"input"' in body):
            try:
                data = json.loads(body)
            except ValueError:
                data = None
        if isinstance(data, dict) and new_turn(data.get("input") or []):
            account = self.accounts.turn_start(account)  # planned switches happen between turns
        tried, drop_unknown = set(), False
        while True:
            target, payload = account, body
            if data is not None:
                prepared, stay = self.router.prepare(data, account, drop_unknown, exclude=tried)
                if stay is not None:
                    target, prepared = stay, data
                if prepared is not data:
                    payload = json.dumps(prepared, separators=(",", ":"), ensure_ascii=False).encode()
            outgoing = dict(headers)
            try:
                token, workspace = self.accounts.credentials(target)
            except Exception:
                token, workspace = None, None
            if token:
                outgoing["Authorization"] = "Bearer " + token
                if workspace:
                    outgoing["ChatGPT-Account-Id"] = workspace
            else:  # no usable saved login: send the session's own rather than none
                outgoing.update(own)
            response, status = self.open(h.command, path, outgoing, payload)
            if response is None:
                return self.reply(h, 502, json.dumps({"error": {"message": f"Could not reach ChatGPT: {status}"}}).encode())
            if status in (400, 429):
                error_body = response.read()
                limited, resets = usage_limit(status, error_body)
                if limited:
                    tried.add(target)
                    following = self.accounts.limit_hit(target, resets)
                    if following is not None and following not in tried:
                        account, drop_unknown = following, False
                        continue
                elif encrypted_rejected(status, error_body) and not drop_unknown:
                    drop_unknown = True
                    continue
                return self.reply(h, status, error_body, response.headers)
            return self.forward_response(h, response, status, target, data, outgoing)

    def open(self, method, path, headers, payload):
        try:
            response = self.opener.open(Request(self.upstream + path, data=payload if method != "GET" else None,
                                                headers=headers, method=method), timeout=TIMEOUT)
        except HTTPError as error:
            response = error
        except (URLError, OSError) as error:
            return None, error
        return response, (response.status if hasattr(response, "status") else response.code)

    def forward(self, h, path, headers, body, account, data):
        response, status = self.open(h.command, path, headers, body)
        if response is None:
            return self.reply(h, 502, json.dumps({"error": {"message": f"Could not reach ChatGPT: {status}"}}).encode())
        return self.forward_response(h, response, status, account, data, headers)

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

    def forward_response(self, h, response, status, account, data, request_headers):
        """Pass the response through as it arrives; note usage and who produced what."""
        if account is not None:
            windows = usage_headers(response.headers)
            if windows:
                try:
                    self.accounts.observe(account, windows)
                except Exception:
                    pass
        h.send_response(status)
        for key, value in response.headers.items():
            if key.lower() not in HOP_BY_HOP:
                h.send_header(key, value)
        h.send_header("Connection", "close")
        h.end_headers()
        h.close_connection = True
        pending = b""
        on_checkpoint = None
        try:
            while True:
                chunk = response.read1(65536) if hasattr(response, "read1") else response.read(65536)
                if not chunk:
                    break
                h.wfile.write(chunk)
                h.wfile.flush()
                if account is not None:
                    pending += chunk
                    *lines, pending = pending.split(b"\n")
                    for line in lines:
                        self._scan(account, line, on_checkpoint)
                    if len(pending) > MAX_BODY:
                        pending = b""
            if account is not None and pending:
                self._scan(account, pending, on_checkpoint)
        except (ConnectionError, OSError):
            pass  # the client went away; stop reading
        finally:
            response.close()

    def _scan(self, account, line, on_checkpoint):
        if b'"encrypted_content"' not in line:
            return
        payload = line[5:] if line.startswith(b"data:") else line
        try:
            self.router.record(account, json.loads(payload), on_checkpoint)
        except ValueError:
            pass
