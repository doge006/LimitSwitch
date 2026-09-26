"""AFK for Claude Code: a StopFailure hook in ~/.claude/settings.json.

When a turn ends on a usage limit, Claude Code runs the hook (error "rate_limit"). The hook
asks this app what to do: the app switches to an account with headroom (a running Claude
Code picks up the new login on its next request) or says how long to wait for a reset. The
hook runs in the background with asyncRewake, so when it finishes with exit code 2 Claude
Code wakes the session with the hook's message and the turn continues, with nobody typing.
Only our own entry is ever added or removed; the rest of settings.json is left as it is.
"""
import json
import os
from pathlib import Path
import sys

from .vault import atomic_write

MARK = "account_switcher_afk"   # appears in our hook's command line, identifies our entry
EVENT = "StopFailure"
TIMEOUT = 6 * 3600              # the hook may wait this long for a usage window to reset


def settings_path():
    root = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(root) if root else Path.home() / ".claude"


def _short(path):
    """A path that needs no quoting in bash, PowerShell or cmd: forward slashes, and the
    8.3 short form on Windows if it contains spaces."""
    text = str(path)
    if sys.platform == "win32" and " " in text:
        import ctypes
        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.GetShortPathNameW(text, buffer, 1024):
            text = buffer.value
    text = text.replace("\\", "/")
    return f'"{text}"' if " " in text else text  # no short name: quote (Git Bash, cmd)


def hook_command(state_file):
    python = Path(sys.executable)
    console = python.with_name("python.exe")
    if python.name.lower() == "pythonw.exe" and console.exists():
        python = console  # hooks need stdout/stderr
    script = Path(__file__).with_name("afk_hook.py")
    return f"{_short(python)} {_short(script)} {_short(state_file)} {MARK}"


def _ours(hook):
    return isinstance(hook, dict) and MARK in str(hook.get("command", ""))


def _load(path):
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    data = json.loads(text)  # invalid JSON: raise, never overwrite the user's file
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    return data


def _without_ours(data):
    groups = (data.get("hooks") or {}).get(EVENT)
    if not isinstance(groups, list):
        return data
    kept = []
    for group in groups:
        if isinstance(group, dict) and isinstance(group.get("hooks"), list):
            hooks = [h for h in group["hooks"] if not _ours(h)]
            if hooks:
                kept.append(dict(group, hooks=hooks))
            elif not group["hooks"]:
                kept.append(group)
        else:
            kept.append(group)
    hooks = dict(data["hooks"])
    if kept:
        hooks[EVENT] = kept
    else:
        hooks.pop(EVENT, None)
    data = dict(data)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    return data


def install(state_file, root=None):
    """Add (or update) our StopFailure hook. Returns True when the file changed."""
    path = (Path(root) if root else settings_path()) / "settings.json"
    data = _load(path)
    entry = {"type": "command", "command": hook_command(state_file), "asyncRewake": True, "timeout": TIMEOUT,
             "rewakeMessage": "Account Switcher:",
             "rewakeSummary": "Account Switcher: continuing after a usage limit"}
    updated = _without_ours(json.loads(json.dumps(data)))  # a real copy: data stays the original
    hooks = dict(updated.get("hooks") or {})
    hooks[EVENT] = list(hooks.get(EVENT) or []) + [{"hooks": [entry]}]
    updated["hooks"] = hooks
    if updated == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(updated, indent=2) + "\n").encode("utf-8"))
    return True


def uninstall(root=None):
    path = (Path(root) if root else settings_path()) / "settings.json"
    try:
        data = _load(path)
    except (ValueError, OSError):
        return False
    updated = _without_ours(data)
    if updated == data:
        return False
    atomic_write(path, (json.dumps(updated, indent=2) + "\n").encode("utf-8"))
    return True
