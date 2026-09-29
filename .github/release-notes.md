A fix release. The app offers it in Settings → **Update to 1.0.3** (or download below).

## What's fixed

- **Windows: installing or updating while Claude Code is open.** Claude Code runs LimitSwitcher's status line script and Auto resume hook with the app's own Python, and those kept files the installer needs to replace ("The following applications are using files… Python"). The installer now ends them first: only processes started from LimitSwitcher's own folder, never another Python. A pending auto-continue is cancelled by the update; the status line comes back on its next refresh.

Everything from [1.0.2](https://github.com/doge006/LimitSwitcher/releases/tag/v1.0.2) and [1.0.1](https://github.com/doge006/LimitSwitcher/releases/tag/v1.0.1) is included.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
