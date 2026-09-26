"""Real accounts: saved logins, live usage, switching and automatic failover.

How it works
- Whatever Claude Code / Codex login is active on this PC is imported automatically, so
  signing in to another account (in the CLI, or via "Add account") adds it here.
- Switching saves the outgoing account's latest tokens, then writes the chosen account's
  login into the files the official clients read. New sessions use it right away.
- Usage is fetched from each provider's own usage endpoint: every 5 minutes (1 minute when
  the account in use is close to a limit), and when the panel or dashboard is opened.
- Token refresh is done here only for accounts that are *not* in use; the live account's
  tokens belong to the official client, so they are never rotated behind its back.
"""
from dataclasses import asdict
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .core import Account, Router
from .providers import PROVIDERS, ProviderError
from .vault import Vault

NORMAL_INTERVAL, URGENT_INTERVAL, FRESH_ENOUGH = 300, 60, 60


class LiveAccounts:
    def __init__(self, notify=lambda *_: None, vault=None, providers=None):
        self.notify = notify
        self.vault = vault or Vault()
        self.providers = providers or {name: cls() for name, cls in PROVIDERS.items()}
        self.lock = threading.RLock()
        meta = self.vault.load_meta()
        self.meta = {"accounts": meta.get("accounts", {}), "autoSwap": meta.get("autoSwap", True)}
        self.active = {}
        self.signatures = {}
        self.backoff = {}  # account id -> monotonic time before which we do not call its API
        self.last_refresh = 0.0
        self.logins = {}   # provider -> running login process info

    # ---------- account list ----------
    def accounts(self):
        with self.lock:
            rows = []
            for account_id, m in self.meta["accounts"].items():
                rows.append(Account(account_id, m["provider"], m.get("email") or m["identity"], 0, 0, 0, 0,
                                    plan=m.get("plan", ""), email=m.get("email", ""), usage=m.get("usage") or [],
                                    status=m.get("status", ""), updated_at=m.get("updatedAt", 0.0)))
            order = {"claude": 0, "codex": 1}
            return sorted(rows, key=lambda a: (order.get(a.provider, 9), a.email or a.alias))

    def save(self):
        self.vault.save_meta(self.meta)

    def find(self, provider, identity):
        return next((i for i, m in self.meta["accounts"].items()
                     if m["provider"] == provider and m["identity"] == identity), None)

    # ---------- live login import ----------
    def sync_live(self, force=False):
        """Import the login each official client is using now; cheap when nothing changed."""
        changed = False
        with self.lock:
            for name, provider in self.providers.items():
                signature = provider.signature()
                if not force and self.signatures.get(name) == signature and name in self.active:
                    continue
                self.signatures[name] = signature
                login = provider.read_live()
                if login is None:
                    changed |= self.active.pop(name, None) is not None
                    continue
                account_id = self.adopt(name, login)
                if self.active.get(name) != account_id:
                    self.active[name] = account_id
                    changed = True
            if changed:
                self.save()
        return changed

    def adopt(self, provider, login):
        """Store (or update) a login; returns its account id."""
        account_id = self.find(provider, login.identity)
        if account_id is None:
            account_id = uuid.uuid4().hex[:12]
            self.meta["accounts"][account_id] = {"provider": provider, "identity": login.identity,
                                                 "addedAt": time.time(), "usage": []}
            self.notify("log", f"Added {provider.title()} account {login.email or login.identity}")
        entry = self.meta["accounts"][account_id]
        entry.update(email=login.email or entry.get("email", ""), plan=login.plan or entry.get("plan", ""))
        self.vault.write_secret(account_id, login.secret)
        if entry.get("status", "").startswith("Login expired"):
            entry["status"] = ""
        return account_id

    # ---------- usage ----------
    def refresh(self, only=None):
        """Fetch usage for every account (or one). Network calls happen outside the lock."""
        self.sync_live()
        with self.lock:
            targets = [(i, dict(m), i == self.active.get(m["provider"])) for i, m in self.meta["accounts"].items()
                       if (only is None or i == only)]
        now = time.monotonic()
        for account_id, meta, is_active in targets:
            if self.backoff.get(account_id, 0) > now:
                continue
            provider = self.providers.get(meta["provider"])
            try:
                secret = self.vault.read_secret(account_id)
            except (OSError, ValueError):
                secret = None
            if provider is None or secret is None:
                self._set(account_id, status="Saved login missing; sign in again")
                continue
            try:
                windows, plan, updated = provider.fetch(secret, allow_refresh=not is_active)
            except ProviderError as error:
                if error.retry_after:
                    self.backoff[account_id] = now + error.retry_after
                message = str(error)
                if error.relogin and is_active:
                    message = f"Waiting for {meta['provider'].title()} to refresh its login"
                self._set(account_id, status=message)
                continue
            except Exception as error:  # a malformed response must not stop the loop
                self._set(account_id, status=f"Usage unavailable ({type(error).__name__})")
                continue
            if updated is not None:
                self.vault.write_secret(account_id, updated)
            self._set(account_id, usage=windows, plan=plan or meta.get("plan", ""), status="", updatedAt=time.time())
        self.last_refresh = time.monotonic()
        with self.lock:
            self.save()
        self.notify("accounts", None)

    def _set(self, account_id, **fields):
        with self.lock:
            if account_id in self.meta["accounts"]:
                self.meta["accounts"][account_id].update(fields)

    # ---------- switching ----------
    def swap(self, account_id, reason="manual"):
        with self.lock:
            target = self.meta["accounts"].get(account_id)
            if target is None:
                raise ValueError("Unknown account")
            name = target["provider"]
            provider = self.providers[name]
            self.sync_live(force=True)
            current = self.active.get(name)
            if current == account_id:
                return
            secret = self.vault.read_secret(account_id)
            if secret is None:
                raise RuntimeError("This account's saved login is missing; sign in again")
            # sync_live(force=True) just saved the outgoing account's newest tokens.
            before = provider.read_live()
            provider.write_live(secret)
            after = provider.read_live()
            if after is None or after.identity != target["identity"]:
                if before is not None:
                    provider.write_live(before.secret)  # put things back exactly as they were
                raise RuntimeError("Switch could not be verified; your previous login was restored")
            self.signatures[name] = provider.signature()
            self.active[name] = account_id
            self.save()
        who = target.get("email") or target["identity"]
        self.notify("log", f"{name.title()} now uses {who}" + (" (automatic)" if reason != "manual" else ""))
        self.notify("accounts", None)

    def auto_swap(self):
        """If an account in use has hit a limit, move to the one with the most headroom."""
        if not self.meta["autoSwap"]:
            return []
        moved = []
        accounts = self.accounts()
        for name in self.providers:
            current = next((a for a in accounts if a.id == self.active.get(name)), None)
            if current is None or current.eligible:
                continue
            candidates = [a for a in accounts if a.provider == name and a.id != current.id
                          and a.eligible and a.headroom > 0 and not a.status]
            if not candidates:
                continue
            best = max(candidates, key=lambda a: a.headroom)
            try:
                self.swap(best.id, reason="auto")
                moved.append(best.id)
            except (RuntimeError, ValueError, OSError) as error:
                self.notify("log", f"Automatic switch failed: {error}")
        return moved

    def remove(self, account_id):
        with self.lock:
            entry = self.meta["accounts"].get(account_id)
            if entry is None:
                raise ValueError("Unknown account")
            if self.active.get(entry["provider"]) == account_id:
                raise RuntimeError("Switch to another account before removing this one")
            del self.meta["accounts"][account_id]
            self.vault.delete_secret(account_id)
            self.save()
        self.notify("accounts", None)

    # ---------- adding accounts ----------
    def add(self, name):
        """Run the official sign-in in an isolated folder so the current login is untouched."""
        provider = self.providers.get(name)
        if provider is None:
            raise ValueError("Unknown provider")
        if name in self.logins:
            raise RuntimeError(f"A {name.title()} sign-in is already open")
        directory = Path(tempfile.mkdtemp(prefix=f"account-switcher-{name}-"))
        command, env = provider.login_command(directory)
        executable = shutil.which(command[0])
        if not executable:
            shutil.rmtree(directory, ignore_errors=True)
            raise RuntimeError(f"{name.title()} CLI not found on PATH")
        flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        process = subprocess.Popen([executable, *command[1:]], env=dict(os.environ, **env), creationflags=flags)
        self.logins[name] = process
        self.notify("log", f"{name.title()} sign-in opened in a new window")
        threading.Thread(target=self._finish_login, args=(name, process, provider.isolated(directory), directory),
                         daemon=True).start()

    def _finish_login(self, name, process, isolated, directory):
        try:
            # Watch for the login file (the CLI may stay open); stop after 10 minutes.
            deadline = time.monotonic() + 600
            login = None
            while time.monotonic() < deadline:
                login = isolated.read_live()
                if login or process.poll() is not None:
                    login = login or isolated.read_live()
                    break
                time.sleep(1.5)
            if login:
                with self.lock:
                    account_id = self.adopt(name, login)
                    self.save()
                self.refresh(only=account_id)
            else:
                self.notify("log", f"{name.title()} sign-in closed without a login")
        finally:
            if process.poll() is None:
                process.terminate()
            self.logins.pop(name, None)
            shutil.rmtree(directory, ignore_errors=True)
            self.notify("accounts", None)

    def next_delay(self):
        for account in self.accounts():
            if account.id in self.active.values() and (not account.eligible or 0 <= account.headroom <= 10):
                return URGENT_INTERVAL
        return NORMAL_INTERVAL


class LiveRouter(Router):
    """Router view over LiveAccounts so the controller and UIs work unchanged."""

    def __init__(self, manager):
        self.manager = manager

    @property
    def accounts(self):
        return self.manager.accounts()

    @property
    def active(self):
        return self.manager.active

    @property
    def auto_swap(self):
        return self.manager.meta["autoSwap"]

    @auto_swap.setter
    def auto_swap(self, value):
        with self.manager.lock:
            self.manager.meta["autoSwap"] = bool(value)
            self.manager.save()


class LiveGateway:
    """Controller backend for real accounts. The Recovery lab is demo-only."""
    live = True

    def __init__(self, notify, vault=None, providers=None, background=True):
        self.manager = LiveAccounts(notify, vault, providers)
        self.router = LiveRouter(self.manager)
        self.notify = notify
        self.quota_observed = False
        self.wake = threading.Event()
        self.stopped = False
        self.manager.sync_live(force=True)
        if background:
            threading.Thread(target=self._loop, daemon=True, name="usage-refresh").start()

    def _loop(self):
        delay = 0
        while not self.stopped:
            self.wake.wait(delay)  # sleeps; no work between refreshes
            self.wake.clear()
            if self.stopped:
                break
            try:
                self.manager.refresh()
                self.manager.auto_swap()
            except Exception as error:
                self.notify("log", f"Usage refresh failed: {error}")
            delay = self.manager.next_delay()

    def poke(self, max_age=FRESH_ENOUGH):
        """Refresh soon if data is older than max_age seconds (panel/dashboard opened)."""
        if time.monotonic() - self.manager.last_refresh > max_age:
            self.wake.set()

    def swap(self, account_id):
        self.manager.swap(account_id)
        return next(a for a in self.manager.accounts() if a.id == account_id)

    def reset(self):
        self.wake.set()

    def apply_preferences(self):
        if self.router.auto_swap:
            self.manager.auto_swap()

    # The Recovery lab drives a synthetic proxy; it has no meaning with real accounts.
    def start(self):
        raise RuntimeError("The Recovery lab runs in demo mode only (--demo)")

    def arm(self, mode):
        raise RuntimeError("The Recovery lab runs in demo mode only (--demo)")

    def prepare_continue(self):
        return False

    def close(self):
        self.stopped = True
        self.wake.set()
