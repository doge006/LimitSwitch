"""Dashboard adapter for the compiled fork, using dummy upstreams exclusively."""
from .core import Router, demo_accounts
from .demo import DemoGateway
from experiments.proxy.runtime import ProxyFixture


class ProxyDemoGateway(DemoGateway):
    def __init__(self, notify=lambda *_: None):
        super().__init__(notify)
        self.fixture = ProxyFixture(on_request=self._observed)
        self.failed_id = None

    def start(self):
        self.fixture = ProxyFixture(on_request=self._observed)
        self.fixture.start()
        self.token = self.fixture.api_key
        return self.fixture.base_url

    def _observed(self, event):
        if "/count_tokens" in event["path"]:
            return
        with self.lock:
            key = "claude-" + event["account"].lower()
            self.requests.append({"account": key, "mode": event["mode"]})
            self.requests[:] = self.requests[-100:]
            if event["mode"] == "normal":
                self.router.active["claude"] = key
            else:
                self.quota_observed = True
                if key == self.failed_id:
                    self.router.exhaust(next(a for a in self.router.accounts if a.id == key))
            self.notify("log", f"Proxy upstream {event['account']} · {event['mode']} · {event['model']}")
            self.notify("accounts", None)

    def arm(self, mode):
        self.mode = mode
        self.quota_observed = False
        current = self.router.current("claude")
        self.failed_id = current.id if mode != "normal" else None
        selected = current.id[-1].upper()
        self.fixture.set_active(selected)
        self.fixture.set_auto_swap(self.router.auto_swap)
        for account in self.router.accounts:
            if account.provider == "claude":
                self.fixture.modes[account.id[-1].upper()] = "normal" if account.eligible else "quota"
        if mode == "quota":
            self.fixture.modes[selected] = "quota"
        elif mode == "partial":
            # Deliberately fail native retries too, to reach the controller's
            # settled-failure branch. Restore the reserve only upon Continue.
            self.fixture.modes.update(A="partial", B="partial")
            self.notify("log", "Fault injection: reserve also fails until this interrupted turn settles.")

    def apply_preferences(self):
        if self.fixture.process and self.fixture.process.poll() is None:
            self.fixture.set_auto_swap(self.router.auto_swap)

    def swap(self, account_id):
        account = self.router.swap(account_id)
        if account.provider == "claude" and self.fixture.process and self.fixture.process.poll() is None:
            self.fixture.set_active(account_id[-1].upper())
        self.notify("accounts", None)
        return account

    def prepare_continue(self):
        with self.lock:
            account = self.router.current("claude")
            if not account.eligible:
                account = self.router.fallback("claude")
            if account is None:
                return False
        self.fixture.recover_to(account.id[-1].upper())
        self.quota_observed = False
        self.notify("log", f"Settled failure: reserve restored in fixture; Continue uses {account.alias}.")
        self.notify("accounts", None)
        return True

    def reset(self):
        auto = self.router.auto_swap
        if self.fixture.process and self.fixture.process.poll() is None:
            self.fixture.reset_accounts()
        self.router = Router(demo_accounts())
        self.router.auto_swap = auto
        self.requests.clear()
        self.quota_observed = False

    def close(self):
        self.fixture.stop()
