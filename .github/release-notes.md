The app offers it in Settings → **Update to 1.0.4** (or download below).

## New

- **Customizable taskbar (Windows):** Settings (full view, Taskbar view on) list each display with a left and a right slot. A click cycles a slot through Claude in use, Codex in use, each of your accounts, and Off: Claude and Claude, Codex then Claude, one fixed account, or different accounts on each display. Without changes it looks as before. A full-screen game now hides only the blocks on its own display.

## Fixed

- **Fewer "sign in again":** Claude's refresh tokens are single-use. A switch (manual or Auto swap) could land while Claude Code was renewing its login, and the saved copy of that account ended up with a token Claude Code had already used. Switches now hold Claude Code's own login locks, so Claude Code finishes its renewal first and the app keeps the fresh tokens. Two of the app's own usage checks can also no longer renew the same saved login at once, and a login Claude refused is no longer retried every minute.
- **Name mode:** the "now uses …" notification (and "Added …" / "Signed in as …") showed the email; it now shows the account's name. The full view no longer shows part of the email under the name: just "Show email" until clicked.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
