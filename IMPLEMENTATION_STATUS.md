# Implementation status — 2026-09-26

## Shape of the app

A single tray process (`account_switcher.tray`) hosts the controller and a loopback, token-protected dashboard ("full view").
- **Left-click:** a native panel appears above the tray icon, with accounts, usage, renewal times, click-to-switch, Auto swap/AFK, pop out (pin and drag) and Full view.
- **Right-click:** a custom menu in the same style.
- **`--demo`:** sample accounts and the Recovery lab.

## Working with real accounts

- **Import:** the live Claude Code and Codex logins are imported automatically. Add account runs `claude auth login` / `codex login` in an isolated folder.
- **Storage:** logins are saved DPAPI-encrypted under `%LOCALAPPDATA%\AccountSwitcher`, with metadata and cached usage kept separately and no secrets in them.
- **Usage:** read from `api.anthropic.com/api/oauth/usage` (5-hour, weekly, per-model weekly) and `chatgpt.com/backend-api/wham/usage` (5-hour / weekly / 30-day). It refreshes every 5 minutes (1 minute near a limit), on panel/full-view open, and on request.
- **Tokens:** tokens are refreshed only for accounts that aren't in use; the live login belongs to the official client.
- **Switching:** saves the outgoing tokens, writes the target's login, and verifies it (rolling back on mismatch). MCP credentials and other config keys are preserved.
- **Auto swap:** moves to the account with the most headroom when the account in use hits a limit, with a notification.
- **Remove:** removes a saved account; the account in use can't be removed.

## Limits

- Running sessions keep their account until restarted; switching affects new sessions.
- AFK continuation of a real interactive session isn't built. It needs a ConPTY supervisor or Claude Code hooks. The Recovery lab demonstrates it in structured mode only.
- If an account is also signed in elsewhere and refreshes its token there, the saved copy expires ("Sign in again").
- Real provider endpoints can't be reached from the development container. They follow the vendored Codex Vitals clients and are exercised against a fake API; first real use needs a check on your PC.

## Evidence

- 45 tests on Linux (2 skip without the built proxy): core, web, tray, panel renderer, and the real-account backend against fake login files and a fake provider API (import, add, switch round-trip with token capture, refresh ownership, auto swap, controller integration).
- The same suites pass under Wine with Windows Python 3.12, including real DPAPI encryption.
- An interactive Wine harness with the Win32 tray in real-account mode passes: left-click panel; click-to-switch shows "Switching…" then rewrites the login files; pinned panel is draggable and ignores click-away; unpinned closes; the right-click menu toggles AFK and opens the panel.
- Idle tray process (Linux sample): about 31 MB RSS, 3 threads, 0 CPU ticks, 0 context switches; plus one usage check every 5 minutes in real mode.
