"""The app's version and how this copy was installed.

VERSION is bumped for each release; the release workflow tags `v<VERSION>` and publishes
LimitSwitch-Setup.exe. An installed copy has its own Python in `runtime/` and the
uninstaller next to it; a copy from source has `.git`.
"""
from pathlib import Path
import sys

VERSION = "1.0.0"
REPO = "doge006/Account-Switcher"
ROOT = Path(__file__).resolve().parent.parent


def install_kind():
    if (ROOT / "unins000.exe").exists():
        return "installer"
    if (ROOT / ".git").exists():
        return "git"
    return "other"


def launcher():
    """Command that starts the app quietly (start at sign-in, shortcuts): the installed copy's
    exe, else windowless Python running LimitSwitch.pyw."""
    exe = ROOT / "LimitSwitch.exe"
    if install_kind() == "installer" and exe.exists():
        return f'"{exe}"'
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    if windowless.exists():
        python = windowless
    return f'"{python}" "{ROOT / "LimitSwitch.pyw"}"'


def newer(latest, current=VERSION):
    """Is version string `latest` (v1.2.3 or 1.2.3) newer than `current`?"""
    def parts(value):
        numbers = []
        for piece in str(value).lstrip("vV").split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            numbers.append(int(digits) if digits else 0)
        return tuple(numbers + [0] * (3 - len(numbers)))
    try:
        return parts(latest) > parts(current)
    except ValueError:
        return False
