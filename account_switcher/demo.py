"""Demo mode (--demo): synthetic sample accounts, no real logins and no network. Used for
screenshots and the Windows smoke test."""
import threading

from .core import Router, demo_accounts


class DemoGateway:
    live = False

    def __init__(self, notify=lambda *_: None):
        self.router = Router(demo_accounts())
        self.notify = notify
        self.lock = threading.RLock()

    def reset(self):
        """Refresh in demo mode: fresh sample accounts."""
        with self.lock:
            auto = self.router.auto_swap
            self.router = Router(demo_accounts())
            self.router.auto_swap = auto

    def close(self):
        pass
