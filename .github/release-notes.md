A fix release. The app offers it in Settings → **Update to 1.0.2** (or download below).

## What's fixed

- **Windows: updating from the app** could fail with "The download failed" when an earlier download of the same installer was still in the Temp folder. Each try now downloads under its own name, and the silent installer keeps a log (`update-install.log` in `%LOCALAPPDATA%\AccountSwitcher`) in case an update doesn't come back.

Everything from [1.0.1](https://github.com/doge006/LimitSwitcher/releases/tag/v1.0.1) is included.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
