A fix release. The app offers it in Settings → **Update to 1.0.1** (or download below).

## What's fixed

- **Fewer "Rate limited by Claude":** Claude's usage checks now identify as Claude Code, which that endpoint throttles far less, and Claude accounts are checked every minute (a rate limit still slows an account down on its own).
- **Auto resume after a reset:** when the account in use runs out and another one's limit has just reset, the session now continues on that one even if Claude's usage check is rate limited at that moment. Before, it could keep waiting for the first account's reset.
- **Status line keeps up:** a slower usage check no longer steps the numbers back below what Claude Code's status line just reported.
- **What's left is rounded down** everywhere (93.4% used shows 6% left), so it never shows more room than there is and matches Claude Code's own warnings.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
