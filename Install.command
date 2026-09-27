#!/bin/bash
# Install or update LimitSwitcher (macOS). Double-click in Finder, or run in Terminal.
# Options: --branch NAME, --force, --no-launch
cd "$(dirname "$0")" || exit 1
if ! xcode-select -p >/dev/null 2>&1; then
  echo "Installing Apple's command line tools (git and Python)..."
  xcode-select --install
  echo "Finish that installation, then run this again."
  exit 1
fi
PY=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 /usr/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PY="$(command -v "$candidate")"; break
  fi
done
if [ -z "$PY" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "Installing Python with Homebrew..."; brew install python@3.13 && PY="$(brew --prefix)/bin/python3.13"
  else
    echo "Python 3.10 or newer is needed: install it from https://www.python.org/downloads/macos/ and run this again."
    exit 1
  fi
fi
"$PY" scripts/installer.py "$@"
