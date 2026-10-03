"""The optional Claude Code mod (mods/limit-status): installing it and checking on it.

The mod feeds Claude Code's live usage to this app after every turn and shows the app's line in
a spot of its own, so the app never has to wrap the person's status line. It is installed with
Claude Code's own plugin commands, from this repository as a marketplace; nothing but those
commands and the mod's one setting (where this app's local address file is) is written.
"""
import json
import shutil
import subprocess
import sys

from . import processes
from .version import REPO

MARKETPLACE = "limitswitcher"
PLUGIN = "limit-status"
PLUGIN_ID = f"{PLUGIN}@{MARKETPLACE}"
TIMEOUT = 90          # installing fetches the repository
QUIET = 20            # listing what is installed


def _run(args, timeout, stdin_text=None):
    """Runs `claude plugin <args>`. A command that outlives `timeout` is ended with everything it started
    (on Windows `claude` is a shim: ending only it would leave the real process running, and a few of
    those add up)."""
    cli = shutil.which("claude")
    if cli is None:
        raise FileNotFoundError("Claude Code isn't installed (no `claude` command found)")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen([cli, "plugin", *args], stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
    try:
        out, err = process.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        processes.end_tree(process.pid)
        try:
            process.communicate(timeout=5)
        except (subprocess.SubprocessError, OSError):
            pass
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, out, err)


def installed():
    """True or False; None when it can't be told (no `claude` command, or an old one)."""
    try:
        done = _run(["list", "--json"], QUIET)
        if done.returncode != 0:
            return None
        return any(isinstance(p, dict) and p.get("id") == PLUGIN_ID for p in json.loads(done.stdout or "[]"))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def install(state_file, source=REPO):
    """Install (or update) the mod. Returns None when done, else a short reason it didn't work."""
    try:
        added = _run(["marketplace", "add", source], TIMEOUT)
        if added.returncode != 0 and "already" not in (added.stdout + added.stderr).lower():
            return _why(added)
        _run(["marketplace", "update", MARKETPLACE], TIMEOUT)  # the catalog as it is now
        done = _run(["install", PLUGIN_ID, "--config", f"statePath={state_file}"], TIMEOUT)
        there = done.returncode == 0 or "already" in (done.stdout + done.stderr).lower()
        # Installing again changes nothing once it is there: an update is what brings in a newer version.
        updated = _run(["update", PLUGIN_ID], TIMEOUT)
        if not there and updated.returncode != 0:
            return _why(done)
        return None
    except FileNotFoundError as error:
        return str(error)
    except (OSError, subprocess.SubprocessError) as error:
        return f"Couldn't run Claude Code ({type(error).__name__})"


def configure(state_file):
    """Tell the installed mod where this app's local address file is (it moves when the data folder does).
    Returns None when done, else a short reason."""
    try:
        done = _run(["configure", PLUGIN_ID, "--values-stdin"], QUIET, json.dumps({"statePath": str(state_file)}))
        return None if done.returncode == 0 else _why(done)
    except FileNotFoundError as error:
        return str(error)
    except (OSError, subprocess.SubprocessError) as error:
        return f"Couldn't run Claude Code ({type(error).__name__})"


def _why(done):
    text = " ".join((done.stderr or done.stdout or "").split())
    return (text[:160] + "…") if len(text) > 160 else (text or "Claude Code refused the install")
