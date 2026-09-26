"""Account Switcher installer and updater, for Windows and macOS (one script, detects the OS).

    python scripts/installer.py            install, or update an existing install
    python scripts/installer.py --branch X switch to (and update) another branch
    python scripts/installer.py --force    update even with local edits (saved first)
    python scripts/installer.py --no-launch

Started by Install.cmd / Update.cmd (Windows) or Install.command / Update.command (macOS),
which make sure Python and git exist first. Each run:
1. Brings this folder up to date from GitHub (git; local edits and local-only commits are
   never lost: they are stashed or saved to a backup branch, and only with --force).
2. Creates or refreshes a private Python environment in .venv with the requirements for
   this OS (only reinstalled when they change).
3. Adds the app where you expect it: a Start menu shortcut on Windows; "Account Switcher"
   in Applications on macOS (menu bar only, no Dock icon). The app itself registers to
   start at sign-in.
4. Restarts Account Switcher on the new version (or starts it, on first install).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/doge006/Account-Switcher.git"
REQUIREMENTS = ROOT / "requirements-native.txt"
VENV = ROOT / ".venv"
RUNTIME = ROOT / ".runtime"
APP_NAME = "Account Switcher"
MAC_APPS = (Path("/Applications") / f"{APP_NAME}.app", Path.home() / "Applications" / f"{APP_NAME}.app")
MAC_APP = MAC_APPS[1]
WINDOWS, MAC = sys.platform == "win32", sys.platform == "darwin"


def say(text, tone=""):
    colours = {"ok": "32", "warn": "33", "info": "36", "error": "31", "dim": "90"}
    if tone and sys.stdout.isatty() and not WINDOWS:
        text = f"\033[{colours[tone]}m{text}\033[0m"
    print(text, flush=True)


def git(*args, check=True):
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


# ---------- 1. code ----------
def local_work_on_github(target):
    """True when nothing is lost by taking GitHub's version (same files as a GitHub commit,
    or every local-only commit has an equivalent change there)."""
    tree = git("rev-parse", "HEAD^{tree}")
    if tree in git("log", "--format=%T", "--max-count=500", f"origin/{target}").split():
        return True
    done = subprocess.run(["git", "-C", str(ROOT), "cherry", f"origin/{target}", "HEAD"], capture_output=True, text=True)
    return done.returncode == 0 and not any(line.startswith("+") for line in done.stdout.splitlines())


def update_code(branch, force):
    if shutil.which("git") is None:
        raise RuntimeError("git is not installed" + (" (winget install --id Git.Git -e)" if WINDOWS
                                                      else " (xcode-select --install)"))
    if not (ROOT / ".git").exists():
        target = branch or "main"
        say(f"This folder is not a git checkout yet; converting it ({target}).", "warn")
        git("init", "--quiet")
        git("remote", "add", "origin", REPO)
        git("fetch", "--quiet", "origin", target)
        git("checkout", "--quiet", "-f", "-B", target, f"origin/{target}")
        git("branch", "--quiet", f"--set-upstream-to=origin/{target}")
        return "updated", None
    current = git("rev-parse", "--abbrev-ref", "HEAD")
    target = branch or (current if current != "HEAD" else "main")
    say(f"Checking GitHub for updates to {target}...", "dim")
    git("fetch", "--quiet", "origin", target)
    before = git("rev-parse", "HEAD")
    if before == git("rev-parse", f"origin/{target}") and current == target:
        return "current", before
    if git("status", "--porcelain", "--untracked-files=no"):
        if not force:
            say("An update is available, but you have local edits to these files:", "warn")
            print(git("status", "--short", "--untracked-files=no"))
            say("Commit or stash them, or rerun with --force to back them up and update anyway.", "warn")
            return "blocked", before
        git("stash", "push", "--quiet", "-m", "installer backup " + time.strftime("%Y-%m-%d %H:%M"))
        say("Your local edits were saved with 'git stash' (see: git stash list).", "warn")
    if current != target:
        exists = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", f"refs/heads/{target}"],
                                capture_output=True).returncode == 0
        git("switch", "--quiet", target) if exists else git("switch", "--quiet", "-c", target, "--track", f"origin/{target}")
        say(f"Switched to branch {target}.", "info")
    merged = subprocess.run(["git", "-C", str(ROOT), "merge", "--quiet", "--ff-only", f"origin/{target}"],
                            capture_output=True, text=True).returncode == 0
    if not merged:
        safe = local_work_on_github(target)
        if not safe and not force:
            say(f"Your local {target} has commits that are not on GitHub:", "warn")
            print(git("log", "--oneline", "--no-decorate", "--max-count=10", f"origin/{target}..HEAD"))
            say("Rerun with --force to save them on a backup branch and take GitHub's version.", "warn")
            return "blocked", before
        backup = "backup/update-" + time.strftime("%Y%m%d-%H%M%S")
        git("branch", backup)
        git("reset", "--quiet", "--hard", f"origin/{target}")
        say(f"Old history kept on branch {backup}.", "dim" if safe else "warn")
    return "updated", before


# ---------- 2. Python environment ----------
def venv_python(windowless=False):
    if WINDOWS:
        return VENV / "Scripts" / ("pythonw.exe" if windowless else "python.exe")
    return VENV / "bin" / "python3"


def ensure_environment():
    if not venv_python().exists():
        say("Creating a private Python environment...", "info")
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    wanted = hashlib.sha256(REQUIREMENTS.read_bytes() + sys.platform.encode()).hexdigest()
    stamp = VENV / ".requirements.sha256"
    if stamp.exists() and stamp.read_text().strip() == wanted:
        return
    say("Installing requirements...", "info")
    subprocess.run([str(venv_python()), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--upgrade", "pip"], check=False)
    subprocess.run([str(venv_python()), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "-r", str(REQUIREMENTS)], check=True)
    stamp.write_text(wanted)


# ---------- 3. where the app shows up ----------
def windows_shortcut():
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(ROOT / "scripts" / "shortcut.ps1"), "-Python", str(venv_python(windowless=True))], check=False)


def mac_app_location():
    """/Applications when this user can write there (admin accounts can), else ~/Applications."""
    system = MAC_APPS[0]
    return system if os.access(system.parent, os.W_OK) or os.access(system, os.W_OK) else MAC_APPS[1]


def mac_app():
    """Account Switcher.app: a small bundle that starts the app from .venv (LSUIElement: menu
    bar only, no Dock icon). Its output goes to app.log, so a failed start is never silent."""
    global MAC_APP
    MAC_APP = mac_app_location()
    for other in MAC_APPS:
        if other != MAC_APP and (other / "Contents" / "MacOS" / "AccountSwitcher").exists():
            shutil.rmtree(other, ignore_errors=True)  # one copy only (older installs used ~/Applications)
    build_mac_app(MAC_APP)


def build_mac_app(app):
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    plist = {"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": "com.accountswitcher.app",
             "CFBundleExecutable": "AccountSwitcher", "CFBundleIconFile": "AppIcon", "CFBundlePackageType": "APPL",
             "CFBundleShortVersionString": "1.0", "LSUIElement": True, "LSMinimumSystemVersion": "11.0",
             "NSHighResolutionCapable": True}
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    launcher = contents / "MacOS" / "AccountSwitcher"
    log = log_path()
    launcher.write_text(f"""#!/bin/sh
# Starts Account Switcher from its folder. Anything it prints goes to app.log.
PY="{venv_python()}"
LOG="{log}"
if [ ! -x "$PY" ]; then
  /usr/bin/osascript -e 'display alert "Account Switcher did not start" message "Its Python environment is missing. Run Update.command in {ROOT} again." as critical'
  exit 1
fi
mkdir -p "$(dirname "$LOG")"
exec "$PY" "{ROOT / "AccountSwitcher.pyw"}" "$@" >>"$LOG" 2>&1
""")
    launcher.chmod(0o755)
    icon = contents / "Resources" / "AppIcon.icns"
    source = ROOT / "account_switcher" / "static" / "assets" / "switcher.png"
    sizes = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256)]
    try:
        from PIL import Image  # the icon is optional; Pillow is in .venv, not always here
    except ImportError:
        subprocess.run([str(venv_python()), "-c", "from PIL import Image; import sys; "
                        f"Image.open(sys.argv[1]).save(sys.argv[2], sizes={sizes})", str(source), str(icon)], check=False)
    else:
        Image.open(source).save(icon, sizes=sizes)
    subprocess.run(["touch", str(app)], check=False)  # Finder picks up the new icon


# ---------- 4. running copy ----------
def stop_running():
    """Ask a running copy to quit (it undoes its Codex / Claude changes on the way out)."""
    url_file = RUNTIME / "tray.url"
    try:
        url = url_file.read_text(encoding="utf-8").strip()
        base, token = url.split("/#token=")
        request = Request(base + "/api/shutdown", data=b"{}", method="POST",
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        build_opener(ProxyHandler({})).open(request, timeout=5).read()
    except (OSError, ValueError):
        try:
            url_file.unlink()  # stale file from a crashed run
        except OSError:
            pass
        return False
    say("Closing the running Account Switcher...", "info")
    for _ in range(80):
        if not url_file.exists():
            break
        time.sleep(0.25)
    return True


def log_path():
    if MAC:
        return Path.home() / "Library" / "Application Support" / "AccountSwitcher" / "app.log"
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AccountSwitcher" / "app.log"


def started(seconds=20):
    """True once the app is up (it writes its private URL when its server is running)."""
    for _ in range(seconds * 4):
        if (RUNTIME / "tray.url").exists():
            return True
        time.sleep(0.25)
    return False


def start():
    if MAC:
        subprocess.run(["open", str(MAC_APP)], check=False)
        return
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(venv_python(windowless=True)), str(ROOT / "AccountSwitcher.pyw")], cwd=str(ROOT),
                     creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=not WINDOWS)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Install or update Account Switcher.")
    parser.add_argument("--branch", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--skip-code", action="store_true", help="do not touch the code (use this folder as it is)")
    args = parser.parse_args(argv)
    if not (WINDOWS or MAC):
        say("Account Switcher runs on Windows and macOS.", "error")
        return 1
    if sys.version_info < (3, 10):
        say(f"Python 3.10 or newer is needed (this is {sys.version.split()[0]}).", "error")
        return 1
    say(f"Account Switcher installer - {'Windows' if WINDOWS else 'macOS'}", "info")
    try:
        status, before = ("current", None) if args.skip_code else update_code(args.branch, args.force)
        if status == "blocked":
            return 1
        if status == "updated" and before:
            after = git("rev-parse", "HEAD")
            say(f"Updated {before[:7]} -> {after[:7]}:", "ok")
            print(git("log", "--oneline", "--no-decorate", "--max-count=15", f"{before}..{after}"))
        elif status == "current":
            say("Code is up to date.", "ok")
        was_running = stop_running()
        ensure_environment()
        windows_shortcut() if WINDOWS else mac_app()
        if not args.no_launch:
            start()
            if not started():
                say("Account Switcher didn't start. The error:", "error")
                try:
                    print("\n".join(log_path().read_text(encoding="utf-8").splitlines()[-25:]))
                except OSError:
                    python = venv_python()
                    say(f"(no log yet) To see it, run:  \"{python}\" \"{ROOT / 'AccountSwitcher.pyw'}\"", "warn")
                return 1
            say("Restarted Account Switcher." if was_running else "Started Account Switcher.", "ok")
        say("Done." + (" It's in the Start menu." if WINDOWS else f" It's in your menu bar and in {MAC_APP.parent}."), "ok")
        return 0
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        say(str(error), "error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
