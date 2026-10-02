The app offers it in Settings → **Update to 1.0.7** (or download below).

## Fixed

- **Auto resume no longer outlives a closed Claude Code session:** after a usage limit, its background hook waits for an account to have room again (up to 6 hours). It kept waiting after the session was closed, then tried to continue it, so a closed Claude Code could come back to life and use up your limit. The hook now stops as soon as Claude Code is gone.
- **Sign-in windows no longer leave Claude Code or Codex running:** ending a sign-in (Windows) or finishing it (macOS) now ends the whole process, not just its launcher.
- **Fewer "Retrying" notes at start-up:** the first usage check waits 4 seconds, so the network and login files are ready when it asks.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
