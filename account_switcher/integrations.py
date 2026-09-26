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
import sys

from . import claude_hooks, codex_config
from .codex_proxy import DEFAULT_PORT, CodexProxy
from .vault import atomic_write

log = logging.getLogger("account_switcher.integrations")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "AccountSwitcher"


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


def codex_present(codex_home=None):
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return home.exists() or shutil.which("codex") is not None


class Integrations:
    def __init__(self, gateway, hook_url, hook_token, codex_home=None, claude_root=None, upstream=None):
        self.gateway = gateway
        self.manager = gateway.manager
        self.root = Path(self.manager.vault.root)
        self.hook_url, self.hook_token = hook_url, hook_token
        self.codex_home, self.claude_root = codex_home, claude_root
        self.upstream = upstream
        self.proxy = None

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
        proxy = CodexProxy(RoutedAccounts(self.manager), port=port, secret=secret, **kwargs)
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

    def stop(self):
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
            if self.manager.meta.get("afk"):
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
