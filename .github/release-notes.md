The app offers it in Settings → **Update to 1.0.6** (or download below).

## Fixed

- **"Waiting for Claude Code" clears within seconds:** while the login in use waits for Claude Code to renew it, the app now looks at Claude's login file every 20 seconds (no requests to Claude while nothing changed) and checks usage at once when a renewed login appears. Before, the note could stay for about 5 minutes after Claude Code had already renewed it.
- **The note is no longer cut off** on accounts that aren't in use: it now reads "Waiting for Claude Code".

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
