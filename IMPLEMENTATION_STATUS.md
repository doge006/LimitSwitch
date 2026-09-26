# Implementation status — 2026-09-26

## Shape of the app

A single tray process (`account_switcher.tray`) hosts the controller and a loopback, token-protected dashboard. Left-click opens a compact native panel (accounts, usage, one-click swap, Auto swap/AFK, Full view); Full view opens the web dashboard. Right-click gives a short menu. The earlier Tk dashboard and Tk tray popup were removed in favour of the web dashboard. The standalone "web app" launcher is gone too; `python -m account_switcher.web` remains for development.

## Working (with synthetic data)

- Usage windows per account (5-hour, weekly, per-model caps), reset times, and active account per provider.
- Manual swap from the dashboard or the tray panel.
- Auto swap: on a quota failure, moves to the account with the most headroom.
- AFK recovery in the Recovery lab. The official Claude CLI in structured, tool-free mode hits a synthetic mid-response quota failure, the session moves to the reserve account, and one Continue is sent.
- Failover notifications, a status-coloured tray icon and a quota tooltip.
- One-shot `Update.cmd`.

## Not built yet (in priority order)

1. Real usage fetching: proxy quota endpoints or the Codex usage client in `codex-vitals-source`.
2. Encrypted credential storage and real sign-in. The proxy writes plaintext auth files today.
3. AFK for your real interactive Claude Code session (ConPTY or hooks). Today it only works in the lab's structured session.
4. Codex switching beyond display. Tool-side-effect reconciliation, concurrent sessions, VS Code/desktop integration.

## Evidence

- Tests pass for core, web and tray. Tests needing the Claude CLI or built proxy skip when those are absent.
- Idle tray process, 30 s sample on Linux: about 31 MB RSS, 3 threads, 0 CPU ticks, 0 context switches.
- The tray panel's Windows code runs under Wine (Windows Python 3.12 + pystray's Win32 backend): open, hover, click-to-swap, AFK switch, Esc, reopen, Full view and quit all pass. Real Windows still needs a hands-on check of the look, Segoe UI fonts, DPI scaling, notifications and the Edge app window.
- Experiment write-ups: `experiments/*/RESULTS.md`.
