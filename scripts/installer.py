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
PREBUILT_ONLY = False  # --prebuilt-launcher (CI: test the launcher copy kept in the repo)


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
    # main unless asked otherwise: a folder left on a working branch must not keep running old code
    target = branch or "main"
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


PREBUILT_LAUNCHER = ROOT / "scripts" / "mac_launcher"


def python_library():
    """Python's shared library behind .venv (the framework's Python on python.org and
    Homebrew builds, else libpython*.dylib), which the launcher loads at run time."""
    if not venv_python().exists():
        return None
    query = ("import os, sys, sysconfig; v = sysconfig.get_config_var; framework = os.path.join(sys.base_prefix, 'Python'); "
             "print(framework if os.path.isfile(framework) else os.path.join(v('LIBDIR') or '', 'libpython' + (v('LDVERSION') or '') + '.dylib'))")
    done = subprocess.run([str(venv_python()), "-c", query], capture_output=True, text=True)
    path = Path(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else None
    return path if path and path.is_file() else None


def sdks():
    """None (clang's default SDK), then every installed macOS SDK, newest first. A linker older
    than the newest SDK can't read it ("tapi error: unknown architecture"), and an older SDK
    installed alongside still works."""
    found = []
    for root in (Path("/Library/Developer/CommandLineTools/SDKs"),
                 Path("/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs")):
        try:
            found += [p for p in root.iterdir() if p.suffix == ".sdk" and not p.is_symlink()]
        except OSError:
            continue

    def version(path):
        digits = path.stem.replace("MacOSX", "")
        return tuple(int(x) for x in digits.split(".") if x.isdigit()) or (0,)
    return [None] + sorted(found, key=version, reverse=True)


def build_launcher(target, prebuilt_only=False):
    """The app's own executable (scripts/mac_launcher.c): built with this Mac's clang, trying
    each SDK; else the copy CI built from the same source. Returns False when neither works."""
    clang = shutil.which("clang")
    errors = []
    if clang and not prebuilt_only:
        for sdk in sdks():
            command = [clang, "-O2", "-Wall", "-mmacosx-version-min=11.0", "-o", str(target),
                       str(ROOT / "scripts" / "mac_launcher.c")] + (["-isysroot", str(sdk)] if sdk else [])
            build = subprocess.run(command, capture_output=True, text=True)
            if build.returncode == 0:
                return True
            errors.append(f"{sdk.name if sdk else 'default SDK'}: {(build.stderr or '').strip()[-300:]}")
    if PREBUILT_LAUNCHER.is_file():
        shutil.copy2(PREBUILT_LAUNCHER, target)
        target.chmod(0o755)
        if errors:
            say("Built-in launcher used (this Mac's build tools can't link; updating them in Software Update fixes that).", "dim")
        return True
    if errors:
        say("Couldn't build the app launcher (using a script instead):\n" + "\n".join(errors), "warn")
    return False


def launcher_config(resources):
    """Contents/Resources/launcher.conf: what the launcher runs. False without a Python library."""
    library = python_library()
    if not library:
        return False
    (resources / "launcher.conf").write_text(
        "\n".join(str(p) for p in (library, venv_python(), ROOT / "AccountSwitcher.pyw", log_path())) + "\n")
    return True


def build_mac_app(app):
    contents = app / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    log_path().parent.mkdir(parents=True, exist_ok=True)
    # macOS 26 gives menu bar space only when the running program is the bundle's own
    # executable: a script that replaces itself with Python gets an icon of height 0. So the
    # executable is a small native launcher running Python in-process (as py2app apps do).
    native = macos / "Account Switcher"
    built = launcher_config(contents / "Resources") and build_launcher(native, PREBUILT_ONLY)
    if not built and native.exists():
        native.unlink()
    plist = {"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": "com.accountswitcher.app",
             "CFBundleExecutable": native.name if built else "AccountSwitcher", "CFBundleIconFile": "AppIcon",
             "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "1.0", "LSUIElement": True,
             "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
             # Never Rosetta on Apple silicon: the .venv's native libraries can't load there.
             "LSArchitecturePriority": ["arm64", "x86_64"], "LSRequiresNativeExecution": True}
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    # The script: the login item's entry point, and the app itself when the launcher couldn't
    # be built. It starts Python as a child process rather than replacing itself with it,
    # which macOS 26 would again leave without a menu bar icon.
    launcher = macos / "AccountSwitcher"
    log = log_path()
    launcher.write_text(f"""#!/bin/sh
# Starts Account Switcher. Anything it prints goes to app.log.
# Opened by the user it shows its window; --at-login (the login item) starts it quietly.
HERE="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$HERE/Account Switcher" ]; then exec "$HERE/Account Switcher" "$@"; fi
PY="{venv_python()}"
LOG="{log}"
SHOW=--show
if [ "$1" = "--at-login" ]; then SHOW=""; shift; fi
if [ ! -x "$PY" ]; then
  /usr/bin/osascript -e 'display alert "Account Switcher did not start" message "Its Python environment is missing. Run Update.command in {ROOT} again." as critical'
  exit 1
fi
export ACCOUNT_SWITCHER_APP="$(cd "$HERE/../.." && pwd)"
ARCH=""
if [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then ARCH="/usr/bin/arch -arm64"; fi
$ARCH "$PY" "{ROOT / "AccountSwitcher.pyw"}" $SHOW "$@" >>"$LOG" 2>&1
""")
    launcher.chmod(0o755)
    icon = contents / "Resources" / "AppIcon.icns"
    source = ROOT / "account_switcher" / "static" / "assets" / "appicon-mac.png"  # on the macOS icon grid
    sizes = [(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)]
    try:
        from PIL import Image  # the icon is optional; Pillow is in .venv, not always here
    except ImportError:
        subprocess.run([str(venv_python()), "-c", "from PIL import Image; import sys; "
                        f"Image.open(sys.argv[1]).save(sys.argv[2], sizes={sizes})", str(source), str(icon)], check=False)
    else:
        Image.open(source).save(icon, sizes=sizes)
    if MAC:  # one consistent (local) signature for the bundle and the binary it now contains
        subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], capture_output=True, check=False)
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


def stop_leftovers():
    """Windows: end any copy of the app from this folder that is still running (one whose quit
    request could not reach it, e.g. its URL file was lost). Otherwise the new copy would find it,
    hand over to it, and the old code would keep running after an update."""
    if not WINDOWS:
        return
    folder = str(ROOT).replace("'", "''")
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine "
              f"-and $_.CommandLine.Contains('{folder}') "
              "-and $_.CommandLine -match 'AccountSwitcher[.]pyw|account_switcher[.]tray' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }")
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, check=False)
    ended = done.stdout.split()
    if ended:
        say(f"Closed {len(ended)} older Account Switcher process(es) that were still running.", "info")
        try:
            (RUNTIME / "tray.url").unlink()
        except OSError:
            pass
        time.sleep(1)


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


def dump_stuck():
    """A copy that is running but never got ready: have it write where it is stuck to app.log."""
    if WINDOWS:
        return
    done = subprocess.run(["pgrep", "-f", "AccountSwitcher.pyw|MacOS/Account Switcher"], capture_output=True, text=True)
    for pid in done.stdout.split():
        subprocess.run(["kill", "-USR1", pid], check=False)
        say(f"(still running as process {pid}; its stack follows)", "warn")
    time.sleep(1)


def start():
    if MAC:
        subprocess.run(["open", str(MAC_APP)], check=False)
        return
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen([str(venv_python(windowless=True)), str(ROOT / "AccountSwitcher.pyw")], cwd=str(ROOT),
                     creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=not WINDOWS)


class Tee:
    """Everything the installer prints also goes to update.log next to app.log, so what an update
    said can be read afterwards (the window may have closed)."""

    def __init__(self, stream, path):
        self.stream, self.file = stream, None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.file = open(path, "w", encoding="utf-8")
            self.file.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
        except OSError:
            pass

    def write(self, text):
        self.stream.write(text)
        if self.file:
            self.file.write(text)
            self.file.flush()
        return len(text)

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def main(argv=None):
    sys.stdout = Tee(sys.stdout, log_path().with_name("update.log"))
    parser = argparse.ArgumentParser(description="Install or update Account Switcher.")
    parser.add_argument("--branch", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--skip-code", action="store_true", help="do not touch the code (use this folder as it is)")
    parser.add_argument("--prebuilt-launcher", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    global PREBUILT_ONLY
    PREBUILT_ONLY = args.prebuilt_launcher
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
        stop_leftovers()
        ensure_environment()
        windows_shortcut() if WINDOWS else mac_app()
        if not args.no_launch:
            start()
            if not started(45 if MAC else 20):
                say("Account Switcher didn't start. The error:", "error")
                dump_stuck()
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
