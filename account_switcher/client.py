"""Owned official Claude subprocess; structured mode, never injected keystrokes."""
import json
import os
import shutil
import subprocess
import tempfile
import threading

from .core import CONTINUE, Recovery


class ClaudeSession:
    def __init__(self, gateway, notify):
        self.gateway = gateway
        self.notify = notify
        self.recovery = Recovery()
        self.process = None
        self.directory = None
        self.thread = None
        self.busy = False
        self.recovering = False
        self.session_id = None
        self.send_lock = threading.Lock()
        self.partial_seen = False

    def start(self, url):
        executable = shutil.which("claude")
        if not executable:
            raise RuntimeError("Claude CLI was not found on PATH.")
        self.directory = tempfile.TemporaryDirectory(prefix="account-switcher-session-")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_"))}
        env.update({
            "ANTHROPIC_API_KEY": self.gateway.token,
            "ANTHROPIC_BASE_URL": url,
            "CLAUDE_CONFIG_DIR": self.directory.name,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_MAX_RETRIES": "1",
            "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
        })
        command = [executable, "--bare", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--include-partial-messages", "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--system-prompt", "Reply briefly. No tools.", "--model", "claude-sonnet-4-6", "--no-session-persistence"]
        try:
            self.process = subprocess.Popen(command, cwd=self.directory.name, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            self.directory.cleanup()
            self.directory = None
            raise
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()
        self.notify("log", f"Official Claude CLI started · PID {self.process.pid} · isolated, tool-free session.")

    def send(self, text):
        with self.send_lock:
            if self.busy:
                raise RuntimeError("Wait for the current turn to settle, or stop it.")
            if not self.process or self.process.poll() is not None:
                raise RuntimeError("The Claude session has stopped.")
            self.busy = True
            self.partial_seen = False
            try:
                self.process.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                self.busy = False
                raise RuntimeError("Claude closed its input stream.") from None
            self.notify("state", "Running")

    def _read(self):
        while True:
            line = self.process.stdout.readline(2 * 1024 * 1024 + 1)
            if not line:
                break
            if len(line) > 2 * 1024 * 1024:
                self.notify("log", "Client event exceeded the prototype limit. Stopping.")
                self.process.terminate()
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.session_id = event.get("session_id", self.session_id)
            inner = event.get("event", {})
            if inner.get("type") == "content_block_delta":
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta":
                    self.partial_seen = True
                    self.notify("text", delta.get("text", ""))
            if inner.get("type") == "content_block_start" and inner.get("content_block", {}).get("type") in ("tool_use", "server_tool_use"):
                self.recovery.blocked_tools = True
            if event.get("type") != "result":
                continue
            failed = bool(event.get("is_error"))
            will_recover = failed and self.recovery.should_continue(event, self.gateway.quota_observed)
            self.recovering = bool(will_recover)
            self.busy = False
            self.notify("result", {"failed": failed, "session_id": self.session_id})
            if not failed:
                if not self.partial_seen:
                    self.notify("text", event.get("result", ""))
                self.notify("state", "Completed")
                continue
            if will_recover:
                if self.gateway.prepare_continue():
                    if self.recovery.cancelled:
                        self.recovering = False
                        continue
                    self.recovery.submitted()
                    self.notify("log", f"AFK: settled failure → Continue ({self.recovery.attempts}/3).")
                    try:
                        self.send(CONTINUE)
                    except RuntimeError as error:
                        self.notify("log", str(error))
                        self.notify("state", "Needs attention")
                else:
                    self.notify("state", "Waiting for available account / manual swap")
            else:
                self.notify("state", "Interrupted · manual Continue available")
            self.recovering = False
            self.notify("changed", None)
        self.busy = False
        self.recovering = False
        self.notify("state", "Stopped")

    def close(self):
        self.recovery.cancelled = True
        self.recovery.enable(False)
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.thread and self.thread is not threading.current_thread():
                self.thread.join(timeout=2)
            self.process.stdin.close()
            self.process.stdout.close()
            self.process = None
        if self.directory:
            self.directory.cleanup()
            self.directory = None
        self.busy = False
