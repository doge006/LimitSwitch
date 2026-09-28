"""Real accounts: saved logins, live usage, switching and automatic failover.

How it works
- Whatever Claude Code / Codex login is active on this PC is imported automatically, so
  signing in to another account (in the CLI, or via "Add account") adds it here.
- Claude: switching saves the outgoing account's latest tokens, then writes the chosen
  account's login into the files Claude Code reads; running sessions pick it up on their next
  request. AFK: a StopFailure hook asks claude_limit() what to do when a turn hits a limit.
- Codex: while the app runs, Codex sends its requests through the local router
  (codex_proxy.py), so switching just changes the account the router uses; every session
  follows on its next request, and a request that hits a usage limit is retried on another
  account. When the app quits, the chosen account is written into ~/.codex/auth.json.
- Usage is fetched from each provider's own usage endpoint (read-only; it does not use any
  quota). To stay well clear of the endpoints' rate limits, each account has its own schedule:
  the account in use every 5 minutes (3 when close to a limit), others every 15 minutes or
  just after one of their windows resets. Known reset times are applied locally in between,
  requests are spaced out, and 429s back off exponentially (up to an hour).
- Subscription renewal / end dates are checked at most once a day per account; a date you
  enter by hand always wins.
- Tokens in the official login files belong to the official clients and are never rotated
  here. Other saved accounts are refreshed here when needed (for usage checks, and by the
  Codex router just before their access token expires).
"""
import json
import logging
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .core import Account, Router
from .providers import PROVIDERS, ProviderError, _jwt_payload
from .vault import Vault, atomic_write

# Near-live usage for the accounts in use, gently for the rest. Each account also has a pace
# (1 = normal) that doubles when the provider rate limits it and eases back after successes,
# so the app settles at whatever rate the provider accepts.
ACTIVE_INTERVAL, URGENT_INTERVAL, IDLE_INTERVAL = 90, 45, 300  # idle: may be in use in a cloud session or elsewhere
FRESH_ENOUGH = 45           # opening the panel refreshes only data older than this
STALE_AFTER = 1800          # older numbers are shown with their age
MAX_PACE = 8
# Claude's usage API allows few calls (it asked for a 38-minute wait once), so Claude is polled
# gently and follows live through Claude Code's status line instead (no tokens, no API calls).
PROVIDER_INTERVALS = {"claude": (300, 180)}   # (in use, near a limit) when not live
# Accounts not in use: Claude every 15 minutes (and just after a window resets). The same accounts
# are often checked by a second computer too, which shares the same small allowance.
PROVIDER_IDLE = {"claude": 900}
LIVE_FRESH = 900            # status line data this recent counts as live
LIVE_API_INTERVAL = 1800    # while live, the API only fills in the rest (model limits, credits)
SWAP_SETTLE = 20            # status line reports right after a switch may still be the old account
MANUAL_MIN_GAP = 30         # the Refresh button cannot hammer the API
SPACING = 1.5               # seconds between consecutive API calls
SUBSCRIPTION_INTERVAL = 86400
SUBSCRIPTION_LOGIC = 2      # bump when detection changes, so every account is re-checked
MAX_BACKOFF = 3600
WINDOW_KEYS = {300: ("five_hour", "5-hour"), 10080: ("weekly", "Weekly"), 43200: ("monthly", "30-day")}
AFK_NOTE = "The usage limit was reached, so the session moved to another account. Continue exactly where you left off."
AFK_RESUMED = "The usage limit has reset. Continue exactly where you left off."


class LiveAccounts:
    def __init__(self, notify=lambda *_: None, vault=None, providers=None):
        self.notify = notify
        self.vault = vault or Vault()
        self.providers = providers or {name: cls() for name, cls in PROVIDERS.items()}
        self.lock = threading.RLock()
        meta = self.vault.load_meta()
        # Every saved setting comes back (panel size, taskbar view and its display, ...), not just these.
        for entry in (meta.get("accounts") or {}).values():  # a hiccup's back-off doesn't outlive the app
            if entry.get("backoffKind") == "transient":
                entry.update(backoffUntil=0.0, backoffFailures=0, backoffKind=None)
        self.meta = {**meta, "accounts": meta.get("accounts", {}), "autoSwap": meta.get("autoSwap", True),
                     "afk": meta.get("afk", False), "selected": meta.get("selected", {})}
        self.active = {}
        self.live_ids = {}       # provider -> account in the official login file
        self.live_since = {}     # provider -> when the account in the login file last changed
        self.routed = set()      # providers whose requests go through the local router
        self.token_locks = {}
        self.afk_sessions = {}   # Claude session -> {"continues": [times], "waiting": bool}
        self.afk_lock = threading.Lock()  # one limit report at a time (several hooks can ask at once)
        self.afk_continues = []  # every session's continues (times): a cap that no session id can dodge
        self.session_reports = {}  # Claude session -> its last status line numbers
        self.session_moved_at = 0.0  # when a session's numbers last moved (it got a reply)
        self.signatures = {}
        self.last_refresh = 0.0
        self.last_manual = 0.0
        self.spacing = SPACING
        self.on_new_account = lambda: None
        self.on_limit = lambda: None   # a client reported a limit: fetch fresh usage soon
        self.on_swap = lambda provider: None
        self.logins = {}   # provider -> running login process info

    # ---------- account list ----------
    def accounts(self):
        with self.lock:
            rows = []
            now = time.time()
            for account_id, m in self.meta["accounts"].items():
                status = m.get("status", "")
                if status.startswith("Rate limited") and m.get("backoffUntil", 0.0) > now:
                    # The retry time in the current clock setting (it may have changed since)
                    status = f"Rate limited by {m['provider'].title()} · retrying at " + \
                        clock_text(m["backoffUntil"], self.meta.get("clock24"))
                rows.append(Account(account_id, m["provider"], m.get("email") or m["identity"], 0, 0, 0, 0,
                                    plan=m.get("plan", ""), email=m.get("email", ""),
                                    usage=project(m.get("usage") or [], now),
                                    status=status, updated_at=m.get("updatedAt", 0.0),
                                    subscription=subscription_view(m), credits=m.get("credits")))
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
                    self.live_ids.pop(name, None)
                    if name not in self.routed:
                        changed |= self.active.pop(name, None) is not None
                    continue
                account_id = self.adopt(name, login)
                previous, self.live_ids[name] = self.live_ids.get(name), account_id
                if previous != account_id:
                    self.live_since[name] = time.time()
                    if previous is not None:  # a switch by the app itself sets live_ids directly
                        logging.getLogger("account_switcher").warning(
                            "%s's login changed to %s outside LimitSwitcher", name.title(), login.email or "?")
                # Routed: the router decides; only a different login in the file (the user
                # signed in to another account) changes the account in use.
                follow = name not in self.routed or previous != account_id or name not in self.active
                if follow and self.active.get(name) != account_id:
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
            self.on_new_account()  # fetch its usage now rather than at the next scheduled check
        entry = self.meta["accounts"][account_id]
        entry.update(email=login.email or entry.get("email", ""), plan=login.plan or entry.get("plan", ""))
        self.vault.write_secret(account_id, login.secret)
        if entry.get("status", "").startswith("Login expired"):
            entry["status"] = ""
        return account_id

    # ---------- usage ----------
    def due(self, account_id, meta, is_active, now):
        """Wall-clock time this account's usage should next be fetched."""
        updated = meta.get("updatedAt", 0.0)
        last = max(updated, meta.get("attemptedAt", 0.0))
        held = meta.get("backoffUntil", 0.0)   # rate limited: not before this
        pace = meta.get("pace", 1.0)
        if not last:
            return held                     # never fetched: now
        if meta.get("status") or not updated:
            return max(held, last + max(ACTIVE_INTERVAL * pace, 300))   # failing: retry gently, never in a loop
        if is_active:
            if now - meta.get("liveAt", 0.0) < LIVE_FRESH:  # followed live: the API only fills in the rest
                return max(held, meta.get("apiAt", updated) + LIVE_API_INTERVAL)
            usage = project(meta.get("usage") or [], now)
            near = any(w["scope"] == "account" and w["used"] >= 90 for w in usage)
            active, urgent = PROVIDER_INTERVALS.get(meta.get("provider"), (ACTIVE_INTERVAL, URGENT_INTERVAL))
            return max(held, meta.get("apiAt", updated) + (urgent if near else active) * pace)
        # Inactive: usage only changes when a window resets (or if used elsewhere).
        resets = [w["resetsAt"] + 30 for w in meta.get("usage") or [] if w.get("resetsAt") and w["resetsAt"] > updated]
        idle = PROVIDER_IDLE.get(meta.get("provider"), IDLE_INTERVAL) * pace
        return max(held, min([updated + idle] + resets))

    def refresh(self, only=None, force=False, max_age=None):
        """Fetch usage for accounts that are due (or all/one when forced). Network calls happen
        outside the lock and are spaced out so bursts never hit the provider."""
        self.sync_live()
        now = time.time()
        with self.lock:
            targets = []
            for i, m in self.meta["accounts"].items():
                if only is not None and i != only:
                    continue
                is_active = i == self.active.get(m["provider"])
                is_live = i == self.live_ids.get(m["provider"])
                if max_age is not None:
                    due = m.get("updatedAt", 0.0) + (max_age if is_active else max(max_age, 900))
                else:
                    due = 0.0 if (force or only) else self.due(i, m, is_active, now)
                held = m.get("backoffUntil", 0.0) > now
                if held and force and m.get("backoffKind") != "rate":
                    held = False  # Refresh retries after a hiccup (offline, timeout); only a real 429 holds
                if due <= now and not held:
                    targets.append((i, dict(m), is_active, is_live))
        # The accounts in use first: their numbers matter now; the rest can follow a few seconds later.
        targets.sort(key=lambda target: (not target[2], not target[3]))
        fetched = False
        subscriptions = []
        for account_id, meta, is_active, is_live in targets:
            provider = self.providers.get(meta["provider"])
            try:
                secret = self.vault.read_secret(account_id)
            except (OSError, ValueError):
                secret = None
            if provider is None or secret is None:
                self._set(account_id, status="Saved login missing; sign in again")
                continue
            if fetched:
                time.sleep(self.spacing)
            fetched = True
            self._set(account_id, attemptedAt=time.time())
            try:
                # Never rotate the tokens in the official login file: the client owns those.
                windows, plan, updated = provider.fetch(secret, allow_refresh=not (is_active or is_live))
            except ProviderError as error:
                if error.rate_limited:
                    # Wait what the provider asks (Retry-After), else back off exponentially;
                    # a little jitter, and a slower pace from now on. Kept across restarts.
                    failures = meta.get("backoffFailures", 0)
                    wait = error.retry_after or min(MAX_BACKOFF, 60 * 2 ** failures)
                    wait = min(MAX_BACKOFF, max(30, wait)) * random.uniform(1.0, 1.15)
                    self._set(account_id, backoffUntil=time.time() + wait, backoffFailures=failures + 1,
                              backoffKind="rate", pace=min(MAX_PACE, meta.get("pace", 1.0) * 2))
                    logging.getLogger("account_switcher").warning(
                        "%s usage check for %s (%s) rate limited: next try in %d min",
                        meta["provider"], meta.get("email") or account_id, "in use" if is_active else "not in use", wait / 60)
                if error.transient:
                    # A hiccup (503, timeout, offline): keep the numbers and say nothing; retry after
                    # 1, 2, 4... min. Only a problem that lasts gets shown.
                    failures = meta.get("backoffFailures", 0)
                    wait = min(900, 60 * 2 ** failures) * random.uniform(1.0, 1.15)
                    self._set(account_id, backoffUntil=time.time() + wait, backoffFailures=failures + 1,
                              backoffKind="transient")
                    if failures + 1 < 3:
                        continue
                    self._set(account_id, status=f"{meta['provider'].title()}'s usage service isn't answering · retrying")
                    continue
                message = str(error)
                if error.rate_limited:  # temporary: say until when
                    message = f"Rate limited by {meta['provider'].title()} · retrying at " + \
                        clock_text(time.time() + wait, self.meta.get("clock24"))
                if error.relogin and (is_active or is_live):
                    message = f"Waiting for {meta['provider'].title()} to refresh its login"
                self._set(account_id, status=message)
                continue
            except Exception as error:  # a malformed response must not stop the loop
                self._set(account_id, status=f"Usage unavailable ({type(error).__name__})")
                continue
            eased = {"pace": max(1.0, meta.get("pace", 1.0) * 0.85)} if meta.get("pace", 1.0) > 1 else {}
            self._set(account_id, backoffUntil=0.0, backoffFailures=0, backoffKind=None, **eased)
            if updated is not None:
                self.vault.write_secret(account_id, updated)
                secret = updated
            self._set(account_id, usage=windows, plan=plan or meta.get("plan", ""), status="", updatedAt=time.time(), apiAt=time.time(),
                      credits=getattr(provider, "last_credits", None))
            self.record_fields(meta["provider"] + "-usage", getattr(provider, "last_fields", None))
            subscriptions.append((account_id, meta, provider, secret))
            if len(targets) > 1:
                self.notify("accounts", None)  # show each account as it arrives, not after all of them
        for account_id, meta, provider, secret in subscriptions:  # renewal dates after every account's usage
            self.check_subscription(account_id, meta, provider, secret)
        self.last_refresh = time.monotonic()
        with self.lock:
            self.save()
        self.notify("accounts", None)

    def check_subscription(self, account_id, meta, provider, secret):
        """Renewal / end date, at most once a day, never when a manual date is set."""
        fresh = time.time() - meta.get("subscriptionCheckedAt", 0) < SUBSCRIPTION_INTERVAL
        if meta.get("subscriptionManual") or (fresh and meta.get("subscriptionLogic") == SUBSCRIPTION_LOGIC):
            return
        if not hasattr(provider, "subscription"):
            return
        time.sleep(self.spacing)
        estimated = False
        try:
            result = provider.subscription(secret)
            at, ends, paths = result[:3]
            estimated = bool(result[3]) if len(result) > 3 else False
        except Exception:
            at, ends, paths = None, None, []
        self._set(account_id, subscription={"at": at, "ends": ends, "estimated": estimated} if at else None,
                  subscriptionCheckedAt=time.time(), subscriptionLogic=SUBSCRIPTION_LOGIC)
        self.record_fields(meta["provider"], paths)

    def record_fields(self, provider, paths):
        """Keep the *names* of fields the account endpoints return (no values), so renewal
        detection can be matched to what the provider actually sends."""
        if not paths:
            return
        path = self.vault.root / "subscription-fields.json"
        try:
            known = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            known = {}
        known[provider] = sorted(set(known.get(provider, [])) | set(paths))[:400]
        try:
            atomic_write(path, json.dumps(known, indent=2).encode())
        except OSError:
            pass

    def set_subscription(self, account_id, at, ends):
        """Manual renewal / end date from the full view; at=None clears it."""
        with self.lock:
            entry = self.meta["accounts"].get(account_id)
            if entry is None:
                raise ValueError("Unknown account")
            if at is None:
                entry.pop("subscriptionManual", None)
                entry["subscriptionCheckedAt"] = 0  # look it up again
            else:
                entry["subscriptionManual"] = {"at": float(at), "ends": bool(ends)}
            self.save()
        self.notify("accounts", None)

    def next_delay(self):
        """Seconds until the earliest account is due (bounded), so the loop sleeps in between."""
        now = time.time()
        with self.lock:
            dues = [self.due(i, m, i == self.active.get(m["provider"]), now) for i, m in self.meta["accounts"].items()]
        return max(20.0, min([ACTIVE_INTERVAL] + [d - now for d in dues]))

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
            logging.getLogger("account_switcher").warning(
                "switching %s to %s (%s)", name.title(), target.get("email") or "?", reason)
            provider = self.providers[name]
            if self.active.get(name) == account_id and self.live_ids.get(name) == account_id:
                return
            secret = self.vault.read_secret(account_id)
            if secret is None:
                raise RuntimeError("This account's saved login is missing; sign in again")
            self.sync_live(force=True)  # saves the outgoing account's newest tokens first
            # Write the login file too: new sessions (and Codex's own /status) then show the
            # chosen account even without the router; the router covers sessions already open.
            if self.live_ids.get(name) != account_id:
                before = provider.read_live()
                provider.write_live(secret)
                after = provider.read_live()
                if after is None or after.identity != target["identity"]:
                    if before is not None:
                        provider.write_live(before.secret)  # put things back exactly as they were
                    raise RuntimeError("Switch could not be verified; your previous login was restored")
                self.signatures[name] = provider.signature()
                self.live_ids[name] = account_id
                self.live_since[name] = time.time()
            self.active[name] = account_id
            if name in self.routed:
                self.meta["selected"][name] = account_id
            self.save()
        self.on_swap(name)
        if reason == "quiet":
            return
        who = target.get("email") or target["identity"]
        self.notify("log", f"{name.title()} now uses {who}" + (" (automatic)" if reason != "manual" else ""))
        self.notify("accounts", None)

    # ---------- routing (Codex through the local router) ----------
    def enable_routing(self, name):
        with self.lock:
            self.routed.add(name)
            chosen = self.meta["selected"].get(name)
            if chosen in self.meta["accounts"] and self.meta["accounts"][chosen]["provider"] == name:
                self.active[name] = chosen
            elif name in self.active:
                self.meta["selected"][name] = self.active[name]
            self.save()
        self.notify("accounts", None)

    def disable_routing(self, name):
        """Stop routing; write the chosen account into the login file so Codex keeps using it."""
        with self.lock:
            chosen = self.active.get(name)
            self.routed.discard(name)
            self.sync_live(force=True)
            if chosen and chosen != self.active.get(name) and chosen in self.meta["accounts"]:
                try:
                    self.swap(chosen, reason="quiet")
                except (RuntimeError, ValueError, OSError):
                    pass

    def route(self, name):
        with self.lock:
            return self.active.get(name) if name in self.routed else None

    def credentials(self, account_id):
        """Current tokens for a routed account: the official login file's for the account it
        holds (Codex keeps those fresh), our saved copy (refreshed here) for the others."""
        with self.lock:
            entry = self.meta["accounts"].get(account_id)
            live = account_id == self.live_ids.get(entry["provider"]) if entry else False
        if entry is None:
            raise ValueError("Unknown account")
        provider = self.providers[entry["provider"]]
        if live:
            login = provider.read_live()
            if login is not None and login.identity == entry["identity"]:
                tokens = login.secret["auth"]["tokens"]
                return tokens["access_token"], tokens.get("account_id")
        with self.token_locks.setdefault(account_id, threading.Lock()):
            secret = self.vault.read_secret(account_id)
            if secret is None:
                raise RuntimeError("Saved login missing")
            tokens = secret["auth"]["tokens"]
            expires = _jwt_payload(tokens.get("access_token")).get("exp")
            if isinstance(expires, (int, float)) and expires - time.time() < 300:
                secret = provider.refresh(secret, "Codex router, saved login about to expire")
                self.vault.write_secret(account_id, secret)
                tokens = secret["auth"]["tokens"]
            return tokens["access_token"], tokens.get("account_id")

    def limit_hit(self, account_id, resets_at=None):
        """A routed request found this account out of quota. Mark it, and (with Auto swap)
        move to the account with the most headroom. Returns the account to retry on, or None."""
        with self.lock:
            entry = self.meta["accounts"].get(account_id)
            if entry is None:
                return None
            name = entry["provider"]
            if self.active.get(name) != account_id:
                return self.active.get(name)  # a parallel request already moved on
            usage = [dict(w) for w in entry.get("usage") or []]
            window = next((w for w in usage if w["key"] == "five_hour"), None)
            if window is None:
                window = {"key": "five_hour", "label": "5-hour", "scope": "account"}
                usage.insert(0, window)
            window.update(used=100.0, resetsAt=float(resets_at) if resets_at else window.get("resetsAt"))
            entry["usage"] = usage
            auto = self.meta["autoSwap"]
        self.on_limit()
        best = self._best_other(name, account_id, allow_unknown=True) if auto else None
        if best is None:
            self.notify("accounts", None)
            return None
        self.swap(best.id, reason="auto")
        return best.id

    def mark_used_up(self, account_id):
        """A turn just ended on a usage limit: show the account's fullest window as used up (the
        5-hour one when nothing is known), until real numbers say otherwise."""
        with self.lock:
            entry = self.meta["accounts"].get(account_id)
            if entry is None:
                return
            usage = [dict(w) for w in entry.get("usage") or []]
            windows = [w for w in usage if w.get("scope", "account") == "account"]
            if any(w.get("used", 0) >= 100 for w in windows):
                return
            window = max(windows, key=lambda w: w.get("used", 0), default=None)
            if window is None:
                window = {"key": "five_hour", "label": "5-hour", "scope": "account", "resetsAt": None}
                usage.insert(0, window)
            window["used"] = 100.0
            entry["usage"] = usage
            entry.pop("liveAt", None)  # and ask the API again at its normal pace
        self.notify("accounts", None)

    def usable(self, account_id):
        account = next((a for a in self.accounts() if a.id == account_id), None)
        return account is not None and account.eligible and not account.status

    def turn_start(self, account_id):
        """A routed session starts a new turn. If its account is already used up, move now
        (between turns) rather than letting the first request of the turn fail."""
        if not self.meta["autoSwap"]:
            return account_id
        account = next((a for a in self.accounts() if a.id == account_id), None)
        if account is None or account.eligible:
            return account_id
        best = self._best_other(account.provider, account_id)
        if best is None:
            return account_id
        self.swap(best.id, reason="auto")
        return best.id

    def observe(self, account_id, windows, add=False):
        """Live usage the service reported with a response: [(window minutes, used %, reset)].
        add: also windows the account doesn't show yet (the source is certain of them)."""
        changed = False
        with self.lock:
            entry = self.meta["accounts"].get(account_id)
            if entry is None:
                return
            usage = [dict(w) for w in entry.get("usage") or []]
            for minutes, used, reset in windows:
                key = WINDOW_KEYS.get(minutes, (f"window-{minutes * 60}",))[0]
                window = next((w for w in usage if w["key"] == key), None)
                if window is None and add and minutes in WINDOW_KEYS:
                    window = {"key": key, "label": WINDOW_KEYS[minutes][1], "used": 0.0, "resetsAt": None, "scope": "account"}
                    usage.append(window)
                    changed = True
                if window is None or minutes <= 0:
                    continue  # only windows the account really has (headers may report others)
                same_window = reset is not None and window.get("resetsAt") is not None \
                    and abs(window["resetsAt"] - reset) < 120
                if same_window and used < window.get("used", 0.0):
                    continue  # usage never goes down within a window: this report is older than what we have
                if round(window.get("used", -1)) != round(used) or window.get("resetsAt") != reset:
                    changed = True
                window.update(used=float(used), resetsAt=reset)
            entry["usage"] = usage
            entry["updatedAt"] = time.time()
        if changed:
            self.notify("accounts", None)
        return changed

    def statusline(self, limits, session=None):
        """Live usage from a Claude Code status line (rate_limits), for the account signed in to
        Claude Code. Returns the compact status line text.

        Every open Claude Code session reports the numbers from its own last reply, also long
        after it (an idle session may still hold another account's numbers). So a session's report
        counts only once its numbers moved since its previous one, i.e. it just got a reply. A
        session's first report counts only while no other session is busy. Otherwise an old
        session and a fresh one take turns and the bar jumps between their numbers."""
        name = "claude"
        self.sync_live()
        account_id = self.live_ids.get(name)
        now = time.time()
        entry = self.meta["accounts"].get(account_id) if account_id else None
        if entry is None:
            return None
        settled = now - self.live_since.get(name, 0.0) >= SWAP_SETTLE
        if isinstance(limits, dict) and settled:
            windows = []
            for key, minutes in (("five_hour", 300), ("seven_day", 10080)):
                window = limits.get(key)
                if isinstance(window, dict) and isinstance(window.get("used_percentage"), (int, float)):
                    reset = window.get("resets_at")
                    windows.append((minutes, float(window["used_percentage"]),
                                    float(reset) if isinstance(reset, (int, float)) else None))
            # Claude Code repeats its last numbers with every reply, also after a limit when it gets
            # no new ones. Only a report that changes something counts as live; otherwise the API
            # goes back to its normal pace and catches what the status line misses.
            fresh = True
            if session is not None:
                key = tuple(windows)
                with self.lock:
                    previous = self.session_reports.get(session)
                    self.session_reports[session] = key
                    if len(self.session_reports) > 200:  # sessions come and go
                        self.session_reports.pop(next(iter(self.session_reports)))
                    if previous is None:  # a session's first report: fine unless another one is busy now
                        fresh = now - self.session_moved_at >= LIVE_FRESH
                    else:
                        fresh = previous != key
                    if fresh and previous is not None:
                        self.session_moved_at = now
            if windows and fresh and self.observe(account_id, windows, add=True):
                with self.lock:
                    entry["liveAt"] = now
                    if entry.get("status", "").startswith("Rate limited"):
                        entry["status"] = ""  # live numbers: the API's rate limit no longer matters
        parts = ["⇄ LimitSwitcher", self.shown_name(account_id)]
        for window in project(entry.get("usage") or [], now):
            if window.get("scope") == "account" and window["key"] in ("five_hour", "weekly"):
                label = "5h" if window["key"] == "five_hour" else "1w"
                parts.append(f"{label} {max(0, 100 - window['used']):.0f}% left")
        return " · ".join(parts)

    def shown_name(self, account_id):
        """The account as the app shows it: its email, or in name mode its name ("Claude 2" when
        it has none), like every other surface."""
        entry = self.meta["accounts"].get(account_id) or {}
        if not self.meta.get("nameMode"):
            return entry.get("email") or entry.get("identity") or entry.get("provider", "").title()
        if entry.get("label"):
            return entry["label"]
        same = sorted((m.get("email") or m.get("identity") or "", i) for i, m in self.meta["accounts"].items()
                      if m.get("provider") == entry.get("provider"))
        number = next((n for n, (_, i) in enumerate(same, 1) if i == account_id), 1)
        return f"{entry.get('provider', '').title()} {number}"

    def claude_limits(self):
        """The freshest 5-hour and weekly numbers the app has for the account in Claude Code,
        in Claude Code's own rate_limits shape (for the user's own status line command: an idle
        session would otherwise show the numbers from its last reply, however old)."""
        account_id = self.live_ids.get("claude")
        entry = self.meta["accounts"].get(account_id) if account_id else None
        if entry is None:
            return None
        limits = {}
        for window in project(entry.get("usage") or [], time.time()):
            key = {"five_hour": "five_hour", "weekly": "seven_day"}.get(window.get("key"))
            if window.get("scope") == "account" and key:
                limits[key] = {"used_percentage": window["used"], "resets_at": window.get("resetsAt")}
        return limits or None

    def _best_other(self, name, current, allow_unknown=False):
        """The other account with the most headroom. allow_unknown also accepts accounts whose
        usage has not been read yet (after the known ones): a client just hit a limit, and
        trying one is better than stopping."""
        candidates = [a for a in self.accounts() if a.provider == name and a.id != current
                      and a.eligible and not a.status and (a.headroom > 0 or (allow_unknown and a.headroom < 0))]
        return max(candidates, key=lambda a: a.headroom) if candidates else None

    # ---------- AFK (Claude Code) ----------
    def claude_limit(self, session):
        """Called by the StopFailure hook when a Claude turn ended on a usage limit.
        Auto swap moves to another account now; AFK also continues the session (or waits
        for a reset when no account has room). One report at a time: hooks that ask together
        (several waits ending at once) must not all get "continue"."""
        with self.afk_lock:
            answer = self._claude_limit(session)
        logging.getLogger("account_switcher").warning("auto resume: a limit in session %s -> %s",
                                                      (session or "?")[:8], answer.get("action"))
        return answer

    def _claude_limit(self, session):
        afk, auto = bool(self.meta.get("afk")), bool(self.meta["autoSwap"])
        if not (afk or auto):
            return {"action": "stop"}
        current = self.active.get("claude")
        if current is None:
            return {"action": "stop"}
        now = time.time()
        state = self.afk_sessions.setdefault(session or "?", {"continues": [], "waiting": False})
        state["continues"] = [t for t in state["continues"] if now - t < 600]
        self.afk_continues = [t for t in self.afk_continues if now - t < 600]
        if state["continues"] and now - state["continues"][-1] < 90:
            # Reported again right after continuing: the same limit twice, or the continued turn
            # failed at once (the new account is used up too). Either way: no second wake.
            return {"action": "stop"}
        if len(state["continues"]) >= 3 or len(self.afk_continues) >= 6:  # something keeps failing: do not loop
            return {"action": "wait", "seconds": 900}
        before = (self.meta["accounts"].get(current) or {}).get("apiAt")
        self.refresh(only=current)  # fresh numbers for the account that just hit its limit
        if (self.meta["accounts"].get(current) or {}).get("apiAt") == before:
            self.mark_used_up(current)  # none (rate limited, offline): the limit itself says it's used up
        self.on_limit()
        best = self.confirmed_other("claude", current) if auto else None
        if best is not None:
            self.swap(best.id, reason="auto")
            if not afk:
                return {"action": "stop"}  # the next message goes to the new account
            state["continues"].append(now)
            self.afk_continues.append(now)
            state["waiting"] = False
            return {"action": "continue", "message": AFK_NOTE}
        if not afk:
            return {"action": "stop"}
        account = next((a for a in self.accounts() if a.id == current), None)
        if state["waiting"] and account is not None and all(w["used"] < 100 for w in account.windows()):
            state["continues"].append(now)
            self.afk_continues.append(now)
            state["waiting"] = False
            return {"action": "continue", "message": AFK_RESUMED}
        state["waiting"] = True
        resets = [w["resetsAt"] for a in self.accounts() if a.provider == "claude"
                  for w in a.windows() if w["used"] >= 100 and w.get("resetsAt")]
        wait = min(resets) - now + 30 if resets else 900
        return {"action": "wait", "seconds": max(60, min(wait, 6 * 3600))}

    def confirmed_other(self, name, current):
        """The account to continue on after a limit: the best other one whose numbers were just
        checked (or are under 5 minutes old) and show room. Stale numbers can make an account
        that is used up elsewhere look free, and continuing onto it fails at once."""
        tried = set()
        for _ in range(2):  # the best, and if that turns out used up, the next best
            candidates = [a for a in self.accounts() if a.provider == name and a.id != current and a.id not in tried
                          and a.eligible and not a.status]
            if not candidates:
                return None
            best = max(candidates, key=lambda a: a.headroom)
            tried.add(best.id)
            entry = self.meta["accounts"].get(best.id) or {}
            if time.time() - entry.get("updatedAt", 0.0) > 300:
                self.refresh(only=best.id)
                entry = self.meta["accounts"].get(best.id) or {}
            fresh = time.time() - entry.get("updatedAt", 0.0) <= 300
            account = next((a for a in self.accounts() if a.id == best.id), None)
            if fresh and account is not None and account.eligible and account.headroom > 0:
                return account
        return None

    def auto_swap(self):
        """Move off an account that has used up a limit, to the one with the most headroom."""
        if not self.meta["autoSwap"]:
            return []
        moved = []
        accounts = self.accounts()
        for name in self.providers:
            current = next((a for a in accounts if a.id == self.active.get(name)), None)
            if current is None or current.eligible:
                continue
            best = self._best_other(name, current.id)
            if best is None:
                self.recheck_spent(name, current.id)
                continue
            try:
                self.swap(best.id, reason="auto")
                moved.append(best.id)
            except (RuntimeError, ValueError, OSError) as error:
                self.notify("log", f"Automatic switch failed: {error}")
        return moved

    def recheck_spent(self, name, current_id):
        """No other account has room: before giving up, ask again about the ones that only look used
        up because their numbers are over a minute old (at most once a minute; 429 holds stay)."""
        now = time.time()
        if now - getattr(self, "last_recheck", 0.0) < 60:
            return
        self.last_recheck = now
        stale = [i for i, m in self.meta["accounts"].items() if m["provider"] == name and i != current_id
                 and now - m.get("updatedAt", 0.0) > 60 and m.get("backoffKind") != "rate"]
        for account_id in stale:
            self.refresh(only=account_id)
        if stale and self._best_other(name, current_id) is not None:
            self.auto_swap()

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
    def add(self, name, expect=None):
        """Run the official sign-in in an isolated folder so the current login is untouched.
        expect: the account being signed back in ("Sign in again"), to say so if another lands."""
        provider = self.providers.get(name)
        if provider is None:
            raise ValueError("Unknown provider")
        if name in self.logins:
            raise RuntimeError(f"A {name.title()} sign-in is already open")
        # The real path (/private/var/... on macOS): Claude Code names its Keychain item after it.
        directory = Path(tempfile.mkdtemp(prefix=f"account-switcher-{name}-")).resolve()
        command, env = provider.login_command(directory)
        if sys.platform == "darwin":
            # In Terminal: it has the user's PATH (an app opened from Finder does not) and a
            # window to sign in from. A .command file, which Terminal runs by itself: telling
            # Terminal what to do (AppleScript) needs a permission macOS silently refuses.
            # The login is picked up from its folder as it lands.
            import shlex
            script = directory / "Sign in.command"
            script.write_text("#!/bin/sh\n" + "".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items())
                              + " ".join(shlex.quote(c) for c in command)
                              + "\necho\necho 'Done. You can close this window.'\n")
            script.chmod(0o700)
            process = subprocess.Popen(["/usr/bin/open", "-a", "Terminal", str(script)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            watch_process = False
        else:
            executable = shutil.which(command[0])
            if not executable:
                shutil.rmtree(directory, ignore_errors=True)
                raise RuntimeError(f"{name.title()} CLI not found on PATH")
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            process = subprocess.Popen([executable, *command[1:]], env=dict(os.environ, **env), creationflags=flags)
            watch_process = True
        self.logins[name] = process
        self.notify("log", f"{name.title()} sign-in opened in a new window")
        threading.Thread(target=self._finish_login, args=(name, process, provider.isolated(directory), directory, watch_process, expect),
                         daemon=True).start()

    def _finish_login(self, name, process, isolated, directory, watch_process=True, expect=None):
        try:
            # Watch for the login file (the CLI may stay open); stop after 10 minutes.
            deadline = time.monotonic() + 600
            login = None
            while time.monotonic() < deadline:
                login = isolated.read_live()
                if login or (watch_process and process.poll() is not None):
                    login = login or isolated.read_live()
                    break
                time.sleep(1.5)
            if login:
                with self.lock:
                    account_id = self.adopt(name, login)
                    self.save()
                if hasattr(isolated, "forget"):
                    isolated.forget()
                self._set(account_id, backoffUntil=0.0, backoffFailures=0, pace=1.0, status="")
                wanted = self.meta["accounts"].get(expect) if expect else None
                if wanted and account_id != expect:  # the browser was signed in to another account
                    self.notify("log", f"Signed in as {login.email or login.identity}, not "
                                       f"{wanted.get('email') or wanted.get('identity')}. To fix that account, sign out of "
                                       f"claude.ai in the browser (or switch accounts there), then click Sign in again.")
                self.refresh(only=account_id)
            else:
                self.notify("log", f"{name.title()} sign-in closed without a login")
        finally:
            if watch_process and process.poll() is None:
                process.terminate()
            self.logins.pop(name, None)
            shutil.rmtree(directory, ignore_errors=True)
            self.notify("accounts", None)

def clock_text(ts, clock24=None):
    """A time of day in the app's clock setting (Settings → 24-hour clock; unset: the system's)."""
    if clock24 is None:
        from .clock import clock_12h
        clock24 = not clock_12h()
    moment = time.localtime(ts)
    if clock24:
        return time.strftime("%H:%M", moment)
    return time.strftime("%I:%M %p", moment).lstrip("0")


def project(usage, now):
    """Apply reset times that have passed since the last fetch, so the display is right
    without calling the API. Only account-wide and model windows with a known reset."""
    shown = []
    for window in usage:
        if window.get("resetsAt") and window["resetsAt"] <= now:
            window = dict(window, used=0.0, resetsAt=None, projected=True)
        shown.append(window)
    return shown


def subscription_view(meta):
    manual = meta.get("subscriptionManual")
    if manual:
        return dict(manual, source="manual")
    auto = meta.get("subscription")
    if auto and auto.get("at") and auto["at"] > time.time() - 86400:
        return dict(auto, source="auto")
    return None


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
    """Controller backend for real accounts."""
    live = True

    def __init__(self, notify, vault=None, providers=None, background=True):
        self.manager = LiveAccounts(notify, vault, providers)
        self.manager.on_new_account = lambda: self.wake.set() if hasattr(self, "wake") else None
        self.manager.on_limit = lambda: self.wake.set() if hasattr(self, "wake") else None
        self.integrations = None  # Codex router + Claude AFK hook, set up by the tray
        self.router = LiveRouter(self.manager)
        self.notify = notify
        self.quota_observed = False
        self.wake = threading.Event()
        self.stopped = False
        self.force = False
        self.poke_age = None
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
            force, self.force = self.force, False
            poke_age, self.poke_age = self.poke_age, None
            try:
                self.manager.refresh(force=force, max_age=None if force else poke_age)
                if not force and poke_age is not None:
                    self.manager.refresh()  # anything simply due as well
                self.manager.auto_swap()
            except Exception as error:
                self.notify("log", f"Usage refresh failed: {error}")
            delay = self.manager.next_delay()

    def poke(self, max_age=FRESH_ENOUGH):
        """Panel or full view opened: fetch only accounts whose data is older than max_age
        (inactive accounts use at least 15 minutes)."""
        self.poke_age = max(FRESH_ENOUGH, max_age or FRESH_ENOUGH)
        self.wake.set()

    def swap(self, account_id):
        self.manager.swap(account_id)
        return next(a for a in self.manager.accounts() if a.id == account_id)

    def reset(self):
        """Refresh button: fetch everything now, at most every MANUAL_MIN_GAP seconds."""
        if time.monotonic() - self.manager.last_manual >= MANUAL_MIN_GAP:
            self.manager.last_manual = time.monotonic()
            self.force = True
        self.wake.set()

    def set_afk(self, enabled):
        with self.manager.lock:
            self.manager.meta["afk"] = bool(enabled)
            self.manager.save()
        if self.integrations:
            self.integrations.apply_afk()

    def apply_preferences(self):
        if self.router.auto_swap:
            self.manager.auto_swap()

    def close(self):
        self.stopped = True
        self.wake.set()
