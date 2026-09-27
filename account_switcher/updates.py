"""Updates from GitHub Releases: check (once at launch, and from Settings), then install.

- Check: the latest release's tag against version.VERSION. One small HTTPS request to the GitHub
  API; nothing is downloaded until the user asks.
- Install, installed copy (Windows): download the release's LimitSwitch-Setup.exe and run it
  silently into this folder; it closes the app, replaces the files and starts it again. Saved
  accounts and settings live elsewhere (%LOCALAPPDATA%\\AccountSwitcher), so they are untouched.
- Install, macOS app (the DMG's): run scripts/install-mac.sh from GitHub, as the README's install
  command does; it closes the app, replaces it with the new release's and starts it again.
- Install, git copy (the installer's): run the installer, which pulls main, restarts the app and
  saves its output to update.log.
"""
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

from . import tls
from .version import REPO, ROOT, VERSION, install_kind, newer

log = logging.getLogger("account_switcher.updates")
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "LimitSwitch-Setup.exe"
MAC_INSTALL = f"https://raw.githubusercontent.com/{REPO}/main/scripts/install-mac.sh"


def latest_release(timeout=10):
    """{"version", "url", "setup", "notes"} of the newest release, or None when there is none."""
    request = Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "LimitSwitch/" + VERSION})
    try:
        with urlopen(request, timeout=timeout, context=tls.context()) as response:
            data = json.load(response)
    except OSError as error:
        if getattr(error, "code", None) == 404:
            return None  # no release published yet
        raise
    setup_url = next((a.get("browser_download_url") for a in data.get("assets") or [] if a.get("name") == ASSET), None)
    return {"version": str(data.get("tag_name") or "").lstrip("vV"), "url": data.get("html_url"),
            "setup": setup_url, "notes": (data.get("body") or "")[:2000]}


def check():
    """The update state for Settings: {"current", "latest", "available", "checked", "error"}."""
    state = {"current": VERSION, "latest": None, "available": False, "checked": time.time(), "error": None,
             "kind": install_kind()}
    try:
        release = latest_release()
    except Exception as error:  # offline, rate limited, GitHub down: say so, try again later
        state["error"] = "Couldn't reach GitHub"
        log.warning("update check: %s", error)
        return state
    if release:
        state.update(latest=release["version"], url=release["url"], setup=release["setup"],
                     available=newer(release["version"]))
    return state


def install(release):
    """Start installing `release` (a check() result); the caller then quits the app. Returns an
    error message, or None once the update is on its way."""
    kind = install_kind()
    if kind == "git":
        return _run_installer()
    if kind == "mac-app":
        return _run_mac_install()
    if kind != "installer" or sys.platform != "win32":
        return "Update from the Releases page"
    if not release.get("setup"):
        return "This release has no Windows installer"
    try:
        target = Path(tempfile.gettempdir()) / f"LimitSwitch-Setup-{release['latest']}.exe"
        request = Request(release["setup"], headers={"User-Agent": "LimitSwitch/" + VERSION})
        with urlopen(request, timeout=120, context=tls.context()) as response, open(target, "wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as error:
        log.warning("update download: %s", error)
        return "The download failed"
    subprocess.Popen([str(target), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={ROOT}"],
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
    return None


def _run_mac_install():
    """The DMG's app: the install script downloads the release's DMG, closes this app (its
    --quit), replaces it and starts the new one. Its output goes to update.log."""
    from .vault import data_dir
    try:
        out = open(data_dir() / "update.log", "ab")
    except OSError:
        out = subprocess.DEVNULL
    subprocess.Popen(["/bin/bash", "-c", f"curl -fsSL {MAC_INSTALL} | bash -s -- --from-app"],
                     stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return None


def _run_installer():
    """A git copy: the installer pulls main, stops this app and starts the new one."""
    python = Path(sys.executable)
    if sys.platform == "win32" and python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")  # the installer prints; it writes update.log too
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen([str(python), str(ROOT / "scripts" / "installer.py")], cwd=str(ROOT), creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=sys.platform != "win32")
    return None
