"""Updates from GitHub Releases: check (once at launch, and from Settings), then install.

- Check: the latest release's tag against version.VERSION. One small HTTPS request to the GitHub
  API; nothing is downloaded until the user asks.
- Install, portable copy: download the release's Windows zip, then a PowerShell script waits for
  the app to quit, copies the new files over this folder and starts it again. Saved accounts and
  settings live elsewhere (%LOCALAPPDATA%\\AccountSwitcher), so they are untouched.
- Install, git copy (the installer's): run the installer, which pulls main, restarts the app and
  saves its output to update.log.
"""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, urlopen

from .version import REPO, ROOT, VERSION, install_kind, newer

log = logging.getLogger("account_switcher.updates")
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "AccountSwitcher-windows.zip"


def latest_release(timeout=10):
    """{"version", "url", "zip", "notes"} of the newest release, or None when there is none."""
    request = Request(API, headers={"Accept": "application/vnd.github+json", "User-Agent": "AccountSwitcher/" + VERSION})
    try:
        with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            data = json.load(response)
    except OSError as error:
        if getattr(error, "code", None) == 404:
            return None  # no release published yet
        raise
    zip_url = next((a.get("browser_download_url") for a in data.get("assets") or [] if a.get("name") == ASSET), None)
    return {"version": str(data.get("tag_name") or "").lstrip("vV"), "url": data.get("html_url"),
            "zip": zip_url, "notes": (data.get("body") or "")[:2000]}


def _ssl_context():
    import ssl
    try:
        import certifi  # the macOS Python has no system certificates
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


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
        state.update(latest=release["version"], url=release["url"], zip=release["zip"],
                     available=newer(release["version"]))
    return state


def install(release):
    """Start installing `release` (a check() result); the caller then quits the app. Returns an
    error message, or None once the update is on its way."""
    kind = install_kind()
    if kind == "git":
        return _run_installer()
    if kind != "portable" or sys.platform != "win32":
        return "Update from the Releases page"
    if not release.get("zip"):
        return "This release has no Windows download"
    try:
        target = Path(tempfile.gettempdir()) / f"AccountSwitcher-{release['latest']}.zip"
        request = Request(release["zip"], headers={"User-Agent": "AccountSwitcher/" + VERSION})
        with urlopen(request, timeout=120, context=_ssl_context()) as response, open(target, "wb") as out:
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
    except OSError as error:
        log.warning("update download: %s", error)
        return "The download failed"
    script = Path(tempfile.gettempdir()) / "AccountSwitcher-update.ps1"
    script.write_text(SWAP_SCRIPT, encoding="utf-8")
    data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AccountSwitcher"
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
                      str(script), "-Zip", str(target), "-Dir", str(ROOT), "-WaitPid", str(os.getpid()),
                      "-Log", str(data / "update.log")],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
                     close_fds=True)
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


# Waits for the app to quit, puts the new version's files over the old ones, starts it again.
SWAP_SCRIPT = r"""param([string]$Zip, [string]$Dir, [int]$WaitPid, [string]$Log)
$ErrorActionPreference = 'Stop'
function Say($text) { Add-Content -LiteralPath $Log -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $text) }
try {
    Say "Updating $Dir from $Zip"
    Wait-Process -Id $WaitPid -Timeout 60 -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($Dir) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    $unpacked = Join-Path $env:TEMP ('AccountSwitcher-new-' + [guid]::NewGuid())
    Expand-Archive -LiteralPath $Zip -DestinationPath $unpacked -Force
    $source = $unpacked
    $inner = Get-ChildItem -LiteralPath $unpacked -Directory
    if (@($inner).Count -eq 1 -and -not (Test-Path (Join-Path $unpacked 'Account Switcher.exe'))) { $source = $inner[0].FullName }
    robocopy $source $Dir /E /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying the new files failed ($LASTEXITCODE)" }
    Remove-Item -LiteralPath $unpacked -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Zip -Force -ErrorAction SilentlyContinue
    Say 'Updated; starting it again'
} catch {
    Say ('Update failed: ' + $_)
}
Start-Process -FilePath (Join-Path $Dir 'Account Switcher.exe')
"""
