"""Claude Code's own locks, held while the app switches its login.

Claude Code renews its login under two lock directories (npm proper-lockfile: `mkdir` is the
lock, a live holder touches it every 5 s, and a lock untouched for 60 s is stale):
<config dir>/.oauth_refresh.lock, then <config dir>.lock (~/.claude.lock). It reads the login,
renews it over the network and saves it, all inside. A switch landing in that window was
overwritten by the old account's renewed tokens, and the copy the app had just saved of that
account kept a refresh token Claude Code had already used up: "sign in again" later. Holding
the same locks (and ~/.claude.json's, which the switch writes too) makes Claude Code wait, then
re-read, see the new account and skip its renewal. The protocol follows claude-swap's notes
(github.com/realiti4/claude-swap, MIT), checked against Claude Code 2.1.218.
"""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import random
import threading
import time

CREDENTIALS_STALE = 60.0  # Claude Code's credential locks: stale after 60 s
CONFIG_STALE = 10.0       # ~/.claude.json.lock keeps proper-lockfile's defaults
TOUCH_EVERY = 3.0         # a little more often than Claude Code's 5 s
WAIT = 9.0                # Claude Code holds one for a token request: seconds at most


class Busy(RuntimeError):
    """Claude Code held its lock past WAIT."""


@contextmanager
def directory_lock(path, stale, wait=WAIT):
    """A proper-lockfile compatible lock: the directory `path`, kept fresh while held."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while True:
        try:
            os.mkdir(path)
            break
        except FileExistsError:
            pass
        try:
            age = time.time() - os.stat(path).st_mtime
        except FileNotFoundError:
            continue  # released just now
        if age > stale:  # its holder is gone: take it over
            try:
                os.rmdir(path)
            except OSError:
                time.sleep(0.05)
            continue
        if time.monotonic() - start > wait:
            raise Busy("Claude Code is renewing its login; try again in a moment")
        time.sleep(0.25 + random.random() * 0.25)
    done = threading.Event()

    def touch():
        while not done.wait(TOUCH_EVERY):
            try:
                os.utime(path)
            except OSError:
                return

    toucher = threading.Thread(target=touch, daemon=True, name="claude-lock-touch")
    toucher.start()
    try:
        yield
    finally:
        done.set()
        toucher.join(timeout=1.0)
        try:
            os.rmdir(path)
        except OSError:
            pass


@contextmanager
def held(config_dir, config_file, wait=WAIT):
    """Claude Code's credential locks, in its own order, then its config lock."""
    config_dir, config_file = Path(config_dir), Path(config_file)
    with ExitStack() as stack:
        stack.enter_context(directory_lock(config_dir / ".oauth_refresh.lock", CREDENTIALS_STALE, wait))
        stack.enter_context(directory_lock(config_dir.parent / (config_dir.name + ".lock"), CREDENTIALS_STALE, wait))
        stack.enter_context(directory_lock(config_file.parent / (config_file.name + ".lock"), CONFIG_STALE, wait))
        yield
