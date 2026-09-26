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
- **Tokens:** the tokens in the official login files are never rotated here; the clients own them. Other saved accounts are refreshed here when needed.
- **Claude switching:** saves the outgoing tokens, writes the target's login, and verifies it (rolling back on mismatch). MCP credentials and other config keys are preserved. A running Claude Code notices the changed file and uses the new login on its next request (checked in its source: it compares the credentials file's modification time before its token check).
- **Codex routing** (`codex_proxy.py`, `codex_config.py`, `integrations.py`):
  - While the app runs, `~/.codex/config.toml` points `openai_base_url` at a loopback router. The path carries a random secret; port and secret stay the same across restarts.
  - The router adds the chosen account's token and workspace header, so switching applies to every running session's next request.
  - On `usage_limit_reached` it retries the same request on the account with the most headroom (Auto swap).
  - Encrypted items are tied to the account that made them, so the router carries them across:
    - *Compaction checkpoints:* a thread holding one stays on that account while it has quota; after that the checkpoint is left out. No extra requests are ever made.
    - *Reasoning:* replaced by its plain-text summary.
    - *Anything else the service rejects:* one retry without unknown items.
  - Accounts are used to 100%. A request that hits the limit is retried on the next account. If the `x-codex-*-used-percent` response headers already show the account used up, the next turn starts on the next account instead.
  - It answers Codex's WebSocket attempt with 426, which makes Codex use HTTPS right away with no warning.
  - The config also disables request compression (so bodies are readable) and `daemon_auto_start`. Codex's shared background server opens console windows on Windows (openai/codex#44768, #48074). Sessions attach to a running one even with auto-start off, and it keeps its startup settings, which would bypass the router. So the app stops a running one once no session log has been written for 90 seconds (`CodexServerWatch`).
  - Quit writes the chosen account into `auth.json` and restores the config exactly (tagged lines).
- **AFK:**
  - *Claude Code:* a `StopFailure` hook with `asyncRewake` (`claude_hooks.py`, `afk_hook.py`), installed while Auto swap or AFK is on. On `rate_limit` it asks the app, which switches to an account with headroom (Auto swap). With AFK on, it also continues the session: the hook exits 2 with a continuation note (or first waits for the earliest reset when no account has room). There's a loop guard of three continues per ten minutes per session.
  - *Codex:* the router's transparent retry. The session never sees the limit.
- **Auto swap:** moves to the account with the most headroom when the account in use hits a limit (from usage checks, or at once when a client reports it).
- **Start with Windows:** on by default (a `HKCU\...\Run` entry for `AccountSwitcher.pyw`), because Codex's requests go through the app.
- **Remove:** removes a saved account; the account in use can't be removed.

## macOS

- **What exists:** a menu bar app (`macos_app.py`, PyObjC) with a native popover panel (`static/menu.*`, WKWebView), a right-click menu and a full-view window. It uses the Keychain for Claude Code's login and for the vault key, and a LaunchAgent for start at login.
- **What was tested:** everything that runs off a Mac. That covers the Keychain wrapper (against a stand-in for Apple's `security` tool), Claude's Keychain switching, the vault cipher, the app bundle, the login item, and the panel page in light and dark mode.
- **What wasn't:** the PyObjC app itself has not been run on a real Mac yet.

## Installer

`scripts/installer.py` (one script for Windows and macOS): git update with backups, a `.venv` with this OS's requirements, a Start menu shortcut or `~/Applications` app, and a restart. The git logic is covered by tests with throwaway repos. The Windows path was run under Wine: it created the venv, installed the requirements, and the app started and quit from it.

## Limits

- Hidden reasoning can only cross accounts as the plain-text summary ChatGPT returns with it (it's encrypted per account). A compaction summary can't cross at all: a thread that moves after its account is used up loses that older summary (recent messages stay).
- If every account of a provider is out of quota:
  - *Codex:* gets the usage-limit error as usual.
  - *Claude Code:* the AFK hook waits for the earliest reset (up to 6 hours) and then continues.
- Quitting the app while Codex sessions are open: they retry the router's port until the app is back. A session started while the app is closed uses the account written into `auth.json`.
- If an account is also signed in elsewhere and refreshes its token there, the saved copy expires ("Sign in again").
- Real provider endpoints can't be reached from the development container. They follow the vendored Codex Vitals clients and are exercised against a fake API; first real use needs a check on your PC.

## Verifying on a real PC

`Verify-Switch.cmd` (`python -m account_switcher.verify`) checks real switching with two saved accounts. It reads real usage, runs one tiny prompt through the official CLI on account A, simulates A's limit in memory so Auto swap moves to B, runs one prompt on B, then switches back and re-validates every login. The flow is covered by `tests/test_verify.py`, whose stand-in CLI proves each prompt ran on the expected login.

## Evidence

- Real Codex CLI 0.157.1 against the router and a fake ChatGPT backend:
  - its WebSocket attempt got 426 and it used HTTPS without a warning;
  - a usage limit on account x was retried on y, and `codex exec` printed y's answer with exit 0;
  - after switching back to x, `codex exec resume` replayed y's encrypted reasoning; the router sent its summary instead, and x answered;
- Real Claude Code 2.1.283 against a fake Anthropic API:
  - a usage limit fired `StopFailure` with `rate_limit`;
  - the `asyncRewake` hook woke the session and a new request went out.

  (The sandbox injects its own Claude login, so the credentials-file reload itself was checked in Claude Code's code rather than end to end.)

- 94 tests on Linux (2 skip without the built proxy): core, web, tray, panel renderer, the Codex router, config edits, the AFK hook and its decisions, and the real-account backend against fake login files and a fake provider API (import, add, switch round-trip with token capture, refresh ownership, auto swap, controller integration).
- The same suites pass under Wine with Windows Python 3.12, including real DPAPI encryption.
- An interactive Wine harness with the Win32 tray in real-account mode passes: left-click panel; click-to-switch shows "Switching…" then rewrites the login files; pinned panel is draggable and ignores click-away; unpinned closes; the right-click menu toggles AFK and opens the panel.
- Idle tray process in real-account mode, 60 s sample on Linux: 32 MB RSS, 1 wake-up, 0 ms CPU. Usage checks are scheduled per account (5 min in use, 30 min otherwise).
