The first release of LimitSwitcher: every Claude Code and Codex usage limit at a glance, in the Windows tray and taskbar or the macOS menu bar, with one-click account switching.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe`: installs for your user, no admin rights needed |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |

Both bring their own Python, so nothing else is needed. The apps aren't code-signed: on Windows click **More info → Run anyway**; on macOS use the Terminal install, or **System Settings → Privacy & Security → Open Anyway** after opening the DMG's app.

## What it does

- **Every limit at a glance:** 5-hour, weekly and per-model caps for every Claude and Codex account, with reset times and how old the numbers are, in the tray panel, the full view and (Windows) right on the taskbar.
- **One-click switching:** open Claude Code and Codex sessions move to the new account on their next request; nothing needs restarting.
- **Auto swap:** when the account in use hits a limit, the app moves to the account with the most room.
- **Auto resume:** a Claude Code session that stops on a usage limit continues by itself.
- **Checking usage uses none of your quota:** it reads the same read-only endpoints as Claude Code's `/usage` and ChatGPT's usage page.
- **Lightweight:** native windows, no browser or Electron. About 13 MB (working set) in the Windows tray, and 47 MB in the macOS menu bar.

See the [README](https://github.com/doge006/LimitSwitcher#readme) for the details, including exactly what the app changes in Claude Code's and Codex's settings (and puts back on quit).
