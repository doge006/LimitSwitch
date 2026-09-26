# Codex Vitals source review

Reviewed 2026-09-25. Upstream: https://github.com/Joowonoil/Codex-Vitals. Local checkout: `D:\Account-Switcher\codex-vitals-source`. Pinned commit: `88f33f936230f37bb890cd6465e36fdf6ee21455`.

## Recommendation

Use its compact provider/account presentation and selectively adapt its usage normalization and isolated-login patterns. Keep Account Switcher's web frontend and controlled session/failover backend. A wholesale Windows fork brings a separate Tkinter UI, Codex-only support, plaintext credential copies, ambient account mutation, and disruptive desktop restarts. It does not supply seamless mid-response failover or AFK continuation.

A Windows notification-area icon can open our existing local web UI and expose Open/Quit actions. This meets the requested tray access without replacing the web UI. It needs a small resident process while enabled; fully exiting ends residency and failover. A browser remains additional memory usage. No RAM claim for Vitals has been measured in this review.

## Findings tied to source

| Area | Finding | Source |
| --- | --- | --- |
| License | MIT; copying/adapting permitted with copyright and permission notice retained. Keep dependency notices for any bundled dependencies. | `LICENSE`, `THIRD-PARTY-NOTICES.txt`, `windows/third-party/WinSparkle-LICENSE.txt` |
| Windows implementation | Python/Tkinter custom Canvas UI, Pillow, pystray, requests. No browser framework or WebView frontend to drop into our app. | `windows/requirements.txt`, `windows/codexvitals_windows/app.py`, `compact_ui.py` |
| Tray | pystray provides Open, Hide/Show, Refresh All, Add Account, Quit. Closing/hiding its window leaves the process active. | `app.py:_setup_tray_icon`, `hide_window`, `quit` |
| Account login | Runs the official `codex login` subprocess with isolated `CODEX_HOME`; can cancel/timeout login. Useful pattern to adapt. | `account_manager.py:CodexLoginRunner.run` |
| Credential persistence | Reads/writes full tokens in plaintext `auth.json`, copies into managed homes and auth backups. No DPAPI/keyring calls found in Windows source. Saved profiles under `%APPDATA%/CodexVitals`. | `codex_api.py:_load_credentials`, `_save_credentials`; `account_manager.py:materialize_as_managed`, `_backup_ambient_auth`; `file_locations.py` |
| Swap side effects | Copies selected auth to ambient `~/.codex/auth.json`; rewrites `creator_id` in `.codex-global-state.json` and backup. App unconditionally schedules desktop restart after successful switch. | `account_manager.py:switch_active_account`, `_sync_ambient_global_state`, `_rewrite_creator_id`; `app.py:switch_account` |
| Restart scope | Generated PowerShell force-kills process trees, matching every process named `Codex.exe` case-insensitively plus packaged paths. This can include independent CLI processes. Then restores account-specific Electron session directories and launches desktop. This is not a running-turn handoff. | `codex_desktop.py:build_restart_script` |
| Session mutation | Backup/restore includes Network, Local Storage, Session Storage, Preferences and more. Restore removes destination entries before copying; partial failures are logged and relaunch still proceeds. Need bounded ownership and transaction safeguards before any reuse. | `file_locations.py:DESKTOP_SESSION_STATE_ENTRIES`, `codex_desktop.py:Sync-DesktopSessionState` |
| Windows Claude support | No Claude provider implementation found under `windows/`. Claude native implementation is Swift/macOS with macOS Keychain access. Screenshot showing Claude does not establish Windows feature parity. | `Sources/CodexVitals/ClaudeAccountService.swift`, `ClaudeKeychainStore.swift`, `ClaudeUsageClient.swift`; search of `windows/` |
| Usage | Codex bearer-auth usage request defaults to `https://chatgpt.com/backend-api/wham/usage`; refresh uses `https://auth.openai.com/oauth/token`. Models normalize window durations and remaining percentage. | `codex_api.py:_fetch_usage`, `_refresh`, `_normalize_window_roles`; `models.py` |
| Destination safeguard | Usage base can come from profile config; request attaches bearer token to derived destination. Before reuse, add exact scheme/host allowlisting and redirect restrictions. Prefix checks in upstream are not host validation. | `codex_api.py:_resolve_usage_url`, `_fetch_usage` |
| Updates | Automatic update checks default on. Direct build loads WinSparkle, contacts `ramterstudio.com` appcast, verifies using upstream publisher key. Store builds delegate updates to Store. A private fork should disable/remove upstream updater or use its own controlled signed release channel. | `app_settings.py:AppSettings`, `update_manager.py` |
| Other network/telemetry | No analytics/Sentry/telemetry SDK found in inspected Windows source. Provider usage/refresh and update network requests exist, plus user-opened project/branding links. This is source inspection, not exhaustive dependency/network auditing. | `app.py`, `codex_api.py`, `update_manager.py`, `requirements.txt` |
| Background work | Default refresh every five minutes and recurring Tk event-queue polling; update checks every 24 hours when enabled. Tray mode therefore has resident activity. | `app_settings.py`, `app.py:_schedule_auto_refresh`, `_process_event_queue`; `update_manager.py` |
| Tests | 40 Windows `test_` methods across 10 files; mocked/temp-directory tests cover account switching, generated restart script, API parsing, models, settings, package/update logic and presentation. macOS has additional Swift tests. These are not evidence of real live quota recovery. | `windows/tests/`, `Tests/CodexVitalsTests/` |

## Safe adaptation sequence

1. Apply compact grouped account rows, explicit remaining-vs-used labels, provider accents, active state, usage/reset information to existing web UI.
2. Add optional notification-area shell that opens the authenticated localhost URL; explicit Quit shuts down owned backend/proxy. Keep login-at-startup opt-in.
3. If copying parser/model code, preserve MIT notice and tests, adapt into a provider boundary, add host allowlisting and single-owner token refresh.
4. Implement encrypted local vault and isolated account login. Keep ambient auth untouched for owned sessions. Offer disruptive external-client changes only as a separate explicit operation with clear restart scope.
5. Port Claude behavior deliberately from platform-neutral concepts; replace Keychain integration with Windows DPAPI. Keep account/session affinity and existing AFK recovery safety gates.
6. Measure total backend, tray and proxy resources, with browser memory stated separately. Native upstream appearance does not imply a particular resource footprint.

## Verification and limits

Read repository source, license, dependency list and test files; confirmed commit with `git rev-parse HEAD`; counted 40 Windows test methods with ripgrep. Did not launch upstream application, execute account switching or restart scripts, access credentials, or run its tests. Live auth, memory, Windows Claude support and seamless live quota recovery remain unverified by this review.
