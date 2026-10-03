The app offers it in Settings → **Update to 1.0.11** (or download below).

## Fixed

- **The data folder is called LimitSwitcher now:** your accounts and settings were still kept under `AccountSwitcher` (the app's old name). The first start after updating moves that folder to `LimitSwitcher` by itself (`~/Library/Application Support/LimitSwitcher` on macOS, `account-switcher` to `limitswitcher` under `~/.local/share` on Linux). Nothing is lost: if the folder can't be moved, the old one keeps being used. If you renamed it yourself, yours is used. The Claude Code Status mod is pointed at the new place automatically.
- **Account cards no longer cut off their status text:** "Numbers from 1h 20m ago · che…" on an account you aren't using now shortens to what fits ("1h 20m ago · checking") instead of ending mid-word.
- **Claude Code commands the app runs can't pile up:** a `claude plugin …` command that hangs (to look up or install the Claude Code Status mod) is now ended with everything it started, and an unknown mod state is looked up every 10 minutes, not every minute.
- **A stuck usage refresh leaves a trace:** if a refresh takes more than 3 minutes, `app.log` gets every thread's stack, so a stall (numbers stuck at "1h ago · checking") can be traced.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
