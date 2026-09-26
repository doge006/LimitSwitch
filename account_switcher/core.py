"""Small routing and recovery state machine, independent of the UI and client."""
from dataclasses import dataclass
import time


CONTINUE = (
    "Continue the interrupted task from the last confirmed state. "
    "Check the outcome of any interrupted tool action before repeating it. "
    "Keep the existing scope and approval requirements."
)


@dataclass
class Account:
    id: str
    provider: str
    alias: str
    five_hour: float
    weekly: float
    reset_at: float
    weekly_reset_at: float
    exhausted: bool = False

    @property
    def eligible(self):
        # A reset timestamp alone is insufficient evidence of restored quota.
        return not self.exhausted and self.five_hour < 100 and self.weekly < 100


class Router:
    def __init__(self, accounts):
        self.accounts = accounts
        self.active = {}
        self.auto_swap = True
        for account in accounts:
            self.active.setdefault(account.provider, account.id)

    def current(self, provider):
        return next(a for a in self.accounts if a.id == self.active[provider])

    def swap(self, account_id):
        account = next(a for a in self.accounts if a.id == account_id)
        if not account.eligible:
            raise ValueError("This account has no confirmed available quota.")
        self.active[account.provider] = account.id
        return account

    def exhaust(self, account):
        account.exhausted = True
        account.five_hour = 100

    def fallback(self, provider):
        if not self.auto_swap:
            return None
        for account in self.accounts:
            if account.provider == provider and account.eligible:
                return self.swap(account.id)
        return None


class Recovery:
    """Only a terminal failure plus trusted quota evidence permits a continuation.

    This prototype is deliberately tool-free. A future coding controller must
    implement tool/approval reconciliation before lifting that restriction.
    """
    def __init__(self):
        self.afk = False
        self.enabled_at = 0
        self.attempts = 0
        self.seen = set()
        self.blocked_tools = False
        self.cancelled = False

    def enable(self, enabled):
        self.afk = enabled
        self.enabled_at = time.monotonic() if enabled else 0

    def should_continue(self, event, quota_observed):
        if event.get("type") != "result" or not event.get("is_error"):
            return False
        event_id = event.get("uuid")
        if not event_id or event_id in self.seen:
            return False
        self.seen.add(event_id)
        return (self.afk and quota_observed and not self.cancelled
                and not self.blocked_tools and self.attempts < 3
                and time.monotonic() - self.enabled_at < 24 * 3600)

    def submitted(self):
        self.attempts += 1


def demo_accounts():
    now = time.time()
    return [
        Account("claude-a", "claude", "Claude · Personal (synthetic)", 64, 42, now + 7200, now + 3 * 86400),
        Account("claude-b", "claude", "Claude · Second (synthetic)", 18, 27, now + 12600, now + 5 * 86400),
        Account("codex-a", "codex", "Codex · Personal (synthetic)", 36, 51, now + 5400, now + 2 * 86400),
        Account("codex-b", "codex", "Codex · Second (synthetic)", 8, 12, now + 14400, now + 6 * 86400),
    ]
