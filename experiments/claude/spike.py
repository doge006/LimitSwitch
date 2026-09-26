"""Exercise installed Claude Code against a loopback-only fake Anthropic server.

No real credentials are used. Run: python experiments/claude/spike.py
"""
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run_case(mode):
    started = time.monotonic()
    requests = []
    events = []
    state = {"continue_sent": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            requests.append({"path": self.path, "messages": body.get("messages", []), "stream": body.get("stream")})
            if "count_tokens" in self.path:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"input_tokens":10}')
                return
            count = sum("count_tokens" not in r["path"] for r in requests)
            if (mode == "quota" and count == 1) or (mode == "partial" and not state["continue_sent"] and not body.get("stream")):
                self.send_response(429)
                self.send_header("Content-Type", "application/json")
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(b'{"type":"error","error":{"type":"rate_limit_error","message":"Synthetic quota exhausted"}}')
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
                emit("message_start", {"message": {"id": "msg_fake_" + str(count), "type": "message", "role": "assistant", "model": "claude-sonnet-4-6", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 0}}})
                emit("content_block_start", {"index": 0, "content_block": {"type": "text", "text": ""}})
                partial = mode == "partial" and not state["continue_sent"]
                emit("content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "PARTIAL_BEFORE_FAILURE" if partial else "RECOVERED_OK"}})
                if partial:
                    emit("error", {"error": {"type": "rate_limit_error", "message": "Synthetic quota exhausted mid-stream"}})
                    self.close_connection = True
                    return
                emit("content_block_stop", {"index": 0})
                emit("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5}})
                emit("message_stop", {})
            except (ConnectionError, OSError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    process = None
    with tempfile.TemporaryDirectory(prefix="claude-spike-", dir=ROOT) as isolated:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
        env.update({"ANTHROPIC_API_KEY": "dummy-local-test-only", "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}", "CLAUDE_CONFIG_DIR": isolated, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"})
        env["CLAUDE_CODE_MAX_RETRIES"] = "1"
        cmd = [shutil.which("claude"), "--bare", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages", "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--system-prompt", "Reply briefly. No tools.", "--model", "claude-sonnet-4-6", "--no-session-persistence"]
        output = queue.Queue()
        errors = []
        try:
            process = subprocess.Popen(cmd, cwd=isolated, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            def reader():
                for line in process.stdout:
                    try:
                        output.put(json.loads(line))
                    except json.JSONDecodeError:
                        output.put({"raw": line.rstrip()})
            threading.Thread(target=reader, daemon=True).start()
            threading.Thread(target=lambda: errors.extend(process.stderr.readlines()), daemon=True).start()
            def send(text):
                process.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
                process.stdin.flush()
            send("Say hello.")
            deadline = time.monotonic() + 100
            results = []
            while time.monotonic() < deadline:
                try:
                    event = output.get(timeout=0.5)
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                events.append(event)
                if event.get("type") == "result":
                    results.append(event)
                    if mode == "partial" and len(results) == 1:
                        state["continue_sent"] = True
                        send("Continue")
                    else:
                        break
            process.stdin.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            report = {"mode": mode, "command": cmd, "requests": requests, "events": events, "results": results, "stderr": errors, "exit_code": process.returncode, "continue_sent": state["continue_sent"], "elapsed_seconds": round(time.monotonic() - started, 3)}
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait()
            server.shutdown()
            server.server_close()
    (ROOT / (mode + "-results.json")).write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert results, "Client produced no terminal result"
    assert results[-1].get("is_error") is False, "Recovery did not succeed"
    assert results[-1].get("result") == "RECOVERED_OK", "Unexpected response"
    if mode == "quota":
        assert len(requests) >= 2, "Client did not retry quota error"
    if mode == "partial":
        assert len(results) == 2 and results[0].get("is_error") is True
        assert results[0]["session_id"] == results[1]["session_id"], "Session changed"
        assert any("Continue" in json.dumps(r["messages"]) for r in requests)
        assert "Say hello" in json.dumps(requests[-1]["messages"]), "Original prompt lost"
    print(json.dumps({"mode": mode, "request_count": len(requests), "results": [{"subtype": r.get("subtype"), "is_error": r.get("is_error"), "result": r.get("result"), "session_id": r.get("session_id")} for r in results], "continue_sent": state["continue_sent"], "exit_code": report["exit_code"]}), flush=True)


if __name__ == "__main__":
    print(subprocess.check_output([shutil.which("claude"), "--version"], text=True).strip(), flush=True)
    for case in ("normal", "quota", "partial"):
        run_case(case)
