"""Point Codex at the local router, and put its config back exactly as it was.

Edits to ~/.codex/config.toml (every line the app adds is tagged, every line it replaces is
kept as a tagged comment, so restore() is exact):
- openai_base_url: the local router, so all sessions follow the account chosen in the app.
- features.enable_request_compression = false: the router must read request bodies to drop
  encrypted items another account produced (Codex otherwise zstd-compresses them).
- features.daemon_auto_start = false: Codex's shared background server opens a visible
  console window for every command on Windows (openai/codex#44768, #48074). It is not
  needed once the router does the switching; sessions run in their own terminal again.
"""
import os
from pathlib import Path
import re

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

from .vault import atomic_write

TAG = "# account-switcher"
WAS = "# account-switcher-was: "
FEATURES = {"enable_request_compression": "false", "daemon_auto_start": "false"}
HEADER = re.compile(r"^\s*\[")
FEATURES_HEADER = re.compile(r"^\s*\[\s*features\s*\]\s*(#.*)?$")


def config_path(codex_home=None):
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return home / "config.toml"


def _key(line, name):
    return re.match(rf"^\s*{re.escape(name)}\s*=", line) is not None


def strip(text):
    """The config with every edit of ours undone."""
    lines = []
    for line in text.splitlines():
        if line.startswith(WAS):
            lines.append(line[len(WAS):])
        elif TAG not in line:
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def edited(text, base_url):
    lines = strip(text).splitlines()
    first_table = next((i for i, line in enumerate(lines) if HEADER.match(line)), len(lines))
    for i in range(first_table):
        if _key(lines[i], "openai_base_url") or any(_key(lines[i], "features." + k) for k in FEATURES):
            lines[i] = WAS + lines[i]
    lines.insert(first_table, f'openai_base_url = "{base_url}"  {TAG}')
    ours = [f"{k} = {v}  {TAG}" for k, v in FEATURES.items()]
    header = next((i for i, line in enumerate(lines) if FEATURES_HEADER.match(line)), None)
    if header is None:
        if lines and lines[-1].strip():
            lines.append(f"{TAG}")  # blank separator we can remove again
        lines += [f"[features]  {TAG}"] + ours
    else:
        end = next((i for i in range(header + 1, len(lines)) if HEADER.match(lines[i])), len(lines))
        for i in range(header + 1, end):
            if any(_key(lines[i], k) for k in FEATURES):
                lines[i] = WAS + lines[i]
        lines[header + 1:header + 1] = ours
    return "\n".join(lines) + "\n"


def check(text, base_url):
    if tomllib is None:
        return
    data = tomllib.loads(text)
    features = data.get("features") or {}
    if data.get("openai_base_url") != base_url or any(features.get(k) is not False for k in FEATURES):
        raise ValueError("config edit did not take effect")


def apply(base_url, codex_home=None):
    """Route Codex through base_url. Raises (and leaves the file untouched) if the result
    would not be valid TOML with the intended values."""
    path = config_path(codex_home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    new = edited(text, base_url)
    check(new, base_url)
    if new != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, new.encode("utf-8"))
    return path


def restore(codex_home=None):
    """Undo every edit made by apply(); a no-op when there is none."""
    path = config_path(codex_home)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return
    if TAG not in text:
        return
    new = strip(text)
    atomic_write(path, new.encode("utf-8"))
