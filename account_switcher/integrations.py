"""What the app sets up in Codex and Claude Code while it runs, and takes down on quit.

- Codex: the local router (codex_proxy.py) and the config lines pointing Codex at it. Port
  and path secret are kept across restarts, so sessions that are already open reconnect
  after an update or restart (Codex retries a refused connection).
- Claude Code: the AFK hook (claude_hooks.py) while AFK is on, and the small file that tells
  the hook how to reach the app.
- Windows: start with Windows (on by default), because Codex's requests go through the app.
Quit undoes the Codex and Claude changes and writes the chosen Codex account into
~/.codex/auth.json, so Codex keeps working, on that account, without the app.
"""
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time

from . import claude_hooks, codex_config
from .codex_proxy import DEFAULT_PORT, CodexProxy, ThreadState
from .vault import atomic_write

log = logging.getLogger("account_switcher.integrations")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AccountSwitcher"
THREADS = "codex-threads"   # encrypted: which account made which item, checkpoint texts


class RoutedAccounts:
    """What the router needs from the account manager."""

    def __init__(self, manager, provider="codex"):
        self.manager, self.provider = manager, provider

    def route(self):
        return self.manager.route(self.provider)

    def credentials(self, account_id):
        return self.manager.credentials(account_id)

    def limit_hit(self, account_id, resets_at):
        return self.manager.limit_hit(account_id, resets_at)

    def turn_start(self, account_id):
        return self.manager.turn_start(account_id)

    def usable(self, account_id):
        return self.manager.usable(account_id)

    def observe(self, account_id, windows):
        self.manager.observe(account_id, windows)


def codex_home_path(codex_home=None):
    return Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_present(codex_home=None):
    return codex_home_path(codex_home).exists() or shutil.which("codex") is not None


class CodexServerWatch:
    """Stops Codex's shared background server once it is idle.

    Sessions attach to that server if it is running, even with auto-start off, and it keeps
    the settings it started with, so its sessions would skip the router (no switching) and
    it opens a console window for every command on Windows. Once it is gone, each session
    runs in its own terminal and goes through the router. It is only stopped while no
    session has written anything for a while, so no turn in progress is cut off."""

    QUIET = 90      # seconds without session activity that count as idle
    EVERY = 60

    def __init__(self, codex_home=None, notify=lambda *_: None, cli="codex"):
        self.home = codex_home_path(codex_home)
        self.notify, self.cli = notify, cli
        self.stopped = threading.Event()
        self.tried_at = None

    @property
    def socket(self):
        return self.home / "app-server-control" / "app-server-control.sock"

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="codex-server-watch").start()

    def close(self):
        self.stopped.set()

    def _loop(self):
        while not self.stopped.is_set():
            try:
                self.check()
            except Exception:
                pass
            self.stopped.wait(self.EVERY)

    def running(self):
        try:
            stamp = self.socket.stat().st_mtime
        except OSError:
            return False
        return stamp != self.tried_at  # a socket we already stopped (and that stayed) is stale

    def last_activity(self):
        """Newest write to a session log in the last two day folders (sessions/YYYY/MM/DD)."""
        root = self.home / "sessions"
        days = []
        try:
            for year in sorted(p for p in root.iterdir() if p.is_dir())[-2:]:
                for month in sorted(p for p in year.iterdir() if p.is_dir())[-2:]:
                    days += [p for p in month.iterdir() if p.is_dir()]
        except OSError:
            return 0.0
        newest = 0.0
        for day in sorted(days)[-2:]:
            try:
                newest = max([newest] + [f.stat().st_mtime for f in day.iterdir()])
            except OSError:
                continue
        return newest

    def check(self):
        if not self.running() or time.time() - self.last_activity() < self.QUIET:
            return False
        self.tried_at = self.socket.stat().st_mtime
        codex = shutil.which(self.cli)
        if not codex:
            return False
        done = subprocess.run([codex, "app-server", "daemon", "stop"], capture_output=True, text=True, timeout=120,
                              stdin=subprocess.DEVNULL, env=dict(os.environ, CODEX_HOME=str(self.home)),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if done.returncode == 0:
            self.notify("log", "Stopped Codex's shared background server; Codex sessions now go through the app")
            return True
        return False


class Integrations:
    def __init__(self, gateway, hook_url, hook_token, codex_home=None, claude_root=None, upstream=None):
        self.gateway = gateway
        self.manager = gateway.manager
        self.root = Path(self.manager.vault.root)
        self.hook_url, self.hook_token = hook_url, hook_token
        self.codex_home, self.claude_root = codex_home, claude_root
        self.upstream = upstream
        self.proxy = None
        self.watch = None

    @property
    def state_file(self):
        return self.root / "afk-hook.json"

    # ---------- start / stop ----------
    def start(self):
        atomic_write(self.state_file, json.dumps({"url": self.hook_url, "token": self.hook_token}).encode())
        self.apply_afk()
        if codex_present(self.codex_home):
            self.start_codex()
        if self.manager.meta.get("startWithWindows", True):
            set_start_with_windows(True)

    def start_codex(self):
        settings_path = self.root / "router.json"
        try:
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        port = saved.get("port") if isinstance(saved.get("port"), int) else DEFAULT_PORT
        secret = saved.get("secret") if isinstance(saved.get("secret"), str) else secrets.token_urlsafe(18)
        kwargs = {"upstream": self.upstream} if self.upstream else {}
        vault = self.manager.vault
        state = ThreadState(load=lambda: vault.read_secret(THREADS), save=lambda data: vault.write_secret(THREADS, data))
        proxy = CodexProxy(RoutedAccounts(self.manager), port=port, secret=secret, state=state, **kwargs)
        try:
            base_url = proxy.start()
            codex_config.apply(base_url, self.codex_home)
        except Exception as error:
            proxy.close()
            self.manager.notify("log", f"Codex routing is off: {error}")
            log.warning("codex routing failed: %s", error)
            return
        atomic_write(settings_path, json.dumps({"port": proxy.port, "secret": proxy.secret}).encode())
        self.proxy = proxy
        self.manager.enable_routing("codex")
        self.watch = CodexServerWatch(self.codex_home, self.manager.notify)
        self.watch.start()

    def stop(self):
        if self.watch:
            self.watch.close()
        if self.proxy:
            try:
                self.manager.disable_routing("codex")
            finally:
                try:
                    codex_config.restore(self.codex_home)
                except OSError as error:
                    log.warning("could not restore Codex config: %s", error)
                self.proxy.close()
                self.proxy = None
        try:
            claude_hooks.uninstall(self.claude_root)
        except OSError as error:
            log.warning("could not remove the Claude hook: %s", error)
        try:
            self.state_file.unlink()
        except OSError:
            pass

    # ---------- AFK ----------
    def apply_afk(self):
        try:
            # The hook reports Claude's usage limits: needed for Auto swap and for AFK.
            if self.manager.meta.get("afk") or self.manager.meta.get("autoSwap"):
                claude_hooks.install(self.state_file, self.claude_root)
            else:
                claude_hooks.uninstall(self.claude_root)
        except (OSError, ValueError) as error:
            self.manager.notify("log", f"Couldn't update Claude Code's settings for AFK: {error}")


def launcher():
    """Command that starts the app quietly: windowless Python running AccountSwitcher.pyw."""
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    if windowless.exists():
        python = windowless
    script = Path(__file__).resolve().parent.parent / "AccountSwitcher.pyw"
    return f'"{python}" "{script}"'


def set_start_with_windows(enabled):
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launcher())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
    except OSError as error:
        log.warning("start with Windows: %s", error)
