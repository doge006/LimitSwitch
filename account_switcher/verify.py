"""Verify real account switching end to end on this PC (run with the tray app closed).

    python -m account_switcher.verify            # Codex (default)
    python -m account_switcher.verify --provider claude

Steps, using your saved accounts and the official CLI:
  1. Read real usage for every account of the provider (checks the usage API and logins).
  2. Switch to account A and run one tiny prompt through the official CLI.
  3. Simulate A hitting its limit (in memory only; no quota is burned) and let Auto swap
     pick B; confirm the login files now hold B and that the CLI works on B.
  4. Switch back to the account you started with and re-read usage for all of them,
     confirming no login was broken along the way.
Each prompt is one tiny request ("Reply with exactly: OK"). Nothing else is sent anywhere.
A report (no tokens) is written to %LOCALAPPDATA%\\AccountSwitcher\\verify-<time>.txt.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from .live import LiveAccounts
from .vault import data_dir

PROMPT = "Reply with exactly: OK"
COMMANDS = {
    "codex": ["codex", "exec", "--skip-git-repo-check", PROMPT],
    "claude": ["claude", "-p", PROMPT],
}


class Report:
    def __init__(self):
        self.lines, self.failed = [], False

    def say(self, text=""):
        print(text, flush=True)
        self.lines.append(text)

    def check(self, name, ok, detail=""):
        self.failed |= not ok
        self.say(f"  {'PASS' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")
        return ok


def run_cli(command, timeout=240):
    """Run one prompt through the official CLI in a scratch folder. Returns (ok, short output)."""
    executable = shutil.which(command[0])
    if not executable:
        return False, f"{command[0]} not found on PATH"
    with tempfile.TemporaryDirectory(prefix="account-switcher-verify-") as folder:
        try:
            done = subprocess.run([executable, *command[1:]], cwd=folder, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, "timed out"
    text = (done.stdout or "") + (done.stderr or "")
    tail = " ".join(text.strip().split())[-160:]
    return done.returncode == 0 and "OK" in (done.stdout or ""), tail


def usage_line(account):
    parts = [f"{w['label']} {100 - w['used']:.0f}% left" for w in account.windows()]
    return ", ".join(parts) or "no usage reported"


def verify(manager, provider, command, report):
    manager.spacing = 1.0
    manager.sync_live(force=True)
    accounts = [a for a in manager.accounts() if a.provider == provider]
    report.say(f"{provider.title()} accounts saved: {len(accounts)}")
    if len(accounts) < 2:
        report.check("at least two accounts saved", False,
                     "add the second one first: tray menu > Add account, then run this again")
        return
    original = manager.active.get(provider)

    report.say("\n1. Reading real usage")
    manager.refresh(force=True)
    for account in (a for a in manager.accounts() if a.provider == provider):
        report.check(f"{account.email}: {usage_line(account)}", not account.status, account.status)

    a, b = accounts[0], accounts[1]
    report.say(f"\n2. Switch to {a.email} and run one prompt")
    manager.swap(a.id)
    live = manager.providers[provider].read_live()
    report.check("login files now hold A", live is not None and live.email == a.email, live.email if live else "no login")
    ok, output = run_cli(command)
    report.check("official CLI answered on A", ok, output)

    report.say(f"\n3. Simulate {a.email} hitting its limit (in memory only) and let Auto swap act")
    with manager.lock:
        entry = manager.meta["accounts"][a.id]
        real_usage = entry.get("usage")
        entry["usage"] = [dict(w, used=100.0) if w["scope"] == "account" else w for w in (real_usage or [])] or \
            [{"key": "five_hour", "label": "5-hour", "used": 100.0, "resetsAt": time.time() + 3600, "scope": "account"}]
    auto = manager.meta["autoSwap"]
    manager.meta["autoSwap"] = True
    moved = manager.auto_swap()
    manager.meta["autoSwap"] = auto
    with manager.lock:
        manager.meta["accounts"][a.id]["usage"] = real_usage
    target = next((x for x in manager.accounts() if x.id in moved), None)
    report.check("Auto swap moved to another account", bool(moved), target.email if target else "no switch")
    live = manager.providers[provider].read_live()
    report.check("login files now hold the new account", live is not None and target is not None and live.email == target.email,
                 live.email if live else "no login")
    ok, output = run_cli(command)
    report.check("official CLI answered on the new account", ok, output)

    report.say("\n4. Switch back and re-check every login")
    if original and original != manager.active.get(provider):
        manager.swap(original)
    live = manager.providers[provider].read_live()
    first = next((x for x in accounts if x.id == original), None)
    report.check("back on the account you started with", first is None or (live is not None and live.email == first.email),
                 live.email if live else "no login")
    manager.refresh(force=True)
    for account in (x for x in manager.accounts() if x.provider == provider):
        report.check(f"{account.email} login still valid", not account.status, account.status or usage_line(account))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=sorted(COMMANDS), default="codex")
    args = parser.parse_args(argv)
    runtime = Path(__file__).resolve().parent.parent / ".runtime"
    from .tray import existing_instance
    if any(existing_instance(runtime / name) for name in ("tray.url", "native.url", "web.url")):
        print("Account Switcher is running. Quit it first (tray icon > right-click > Quit), then run this again.")
        return 2
    report = Report()
    report.say(f"Account Switcher switching check · {time.strftime('%Y-%m-%d %H:%M')}")
    manager = LiveAccounts(lambda kind, value: report.say(f"     · {value}") if kind == "log" else None)
    try:
        verify(manager, args.provider, COMMANDS[args.provider], report)
    except Exception as error:  # report, don't hide
        report.check("finished without errors", False, f"{type(error).__name__}: {error}")
    report.say("\nRESULT: " + ("FAILED - see above" if report.failed else "ALL PASSED"))
    path = data_dir() / f"verify-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(report.lines) + "\n", encoding="utf-8")
        print(f"Report saved to {path}")
    except OSError:
        pass
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
