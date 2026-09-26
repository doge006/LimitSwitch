# Account Switcher

A Windows tray app that shows every Claude Code and Codex usage limit at a glance, switches accounts in one click, and can switch automatically when the account in use hits a limit.

## Run it (Windows)

Needs Python 3.12+.

```powershell
python -m pip install -r requirements-native.txt
.\Launch.cmd            # starts the tray and opens the full view
.\Launch.cmd --quiet    # starts in the tray only
.\Launch.cmd --demo     # sample accounts + the Recovery lab, no real logins touched
```

## Your accounts

- **Adding accounts:** whatever Claude Code / Codex login is active on this PC is picked up automatically. Signing in to another account (`claude auth login`, `codex login`, or the apps) adds it too. **Add account** (in the full view or the tray menu) runs the official sign-in in a separate window and an isolated folder, so the login you're using isn't touched.
- **Switching:** switching saves the outgoing account's newest tokens, then writes the chosen account's login into the files the official clients read (`~/.claude/.credentials.json` + `~/.claude.json`, `~/.codex/auth.json`). **New sessions use it right away. Sessions already running keep the account they started with until you restart them.**
- **Usage:** read from each provider's own usage endpoint:
  - Claude: 5-hour, weekly and per-model weekly caps, plus extra usage.
  - Codex: 5-hour, weekly or 30-day windows, plus credits.

  These are read-only status endpoints, the same ones behind Claude Code's `/usage` and ChatGPT's usage page. **Checking usage does not use any of your quota.**
- **Staying clear of rate limits:**
  - The account in use is checked every 5 minutes (2 when it's near a limit).
  - Other accounts are checked every 30 minutes, or just after one of their windows resets, since that's the only time their numbers change.
  - Passed reset times are applied locally without a request.
  - Requests are spaced out.
  - Opening the panel only refetches data older than 2 minutes, and Refresh works at most every 30 seconds.
  - A 429 backs off exponentially, from 5 minutes up to an hour.
- **Subscription:** "Renews Oct 14" or "Ends Oct 14" shows when a subscription renews or has been cancelled.
  - Codex: the paid-through date comes from its login token; cancellation is read best-effort from ChatGPT's account check.
  - Claude: Claude's account profile is checked for renewal fields.
  - Both are checked at most once a day. When nothing is reported, click **Set renewal date** on the card; a date you enter always wins.
  - The names of the fields these endpoints return (never their values) are kept in `subscription-fields.json`, to help match the detection to real responses.
- **Auto swap:** when the account in use hits a limit, Auto swap moves to the account with the most headroom and shows a notification. AFK continuation of an interrupted session is still demo-only (see below).
- **Storage:** saved logins are encrypted with Windows DPAPI (tied to your Windows user) under `%LOCALAPPDATA%\AccountSwitcher`. Nothing is sent anywhere except the providers' own usage and token endpoints.
- **Token ownership:** each account should be managed from here only. If the same account is also signed in elsewhere and refreshes its token there, this copy expires and shows "Sign in again". The in-use account's token is never refreshed by this app; that stays with Claude Code / Codex.

Check the providers' terms for using several subscriptions this way; that's your call.

## Tray

- **Left-click the icon:** a compact panel opens above it. It lists every account by email with its plan and subscription status ("Renews 18d" / "Ends 3d"), plus 5h / 1w / per-model headroom with the time until each resets. Click an account to switch; you'll see "Switching…" until it lands. Clicking the icon again closes the panel, pinned or not.
- **Motion:** hovers fade, switches slide, the "in use" marker cross-fades on a switch, and bars glide to new values. Animation frames are drawn only while something moves.
- **The panel:**
  - **Pop out** (next to **Full view ›**) pins the panel: it stays open and you can drag it by its header. Click it again to put it back.
  - **Full view ›** opens the dashboard in a borderless Edge/Chrome app window.
  - Esc or clicking elsewhere closes an unpinned panel.
- **Right-click:** a menu in the same style: Open panel, Full view, Auto swap, AFK, Refresh usage, Add Claude/Codex account and Quit.
- **Hover:** the tooltip shows the account in use per provider and what's left.
- **The icon's dot:** green, amber or red for the tightest limit in use.
- **Launching again:** opens the running copy's full view instead of starting a second copy.
- **Quit** stops everything the app started. Nothing runs at sign-in.

Resource use: one Python process that sleeps until an account is due for a check or something changes. The panel and menu are native windows drawn with Pillow; they exist only while open. Measured idle over 60 s in real-account mode (Linux, virtual display): 32 MB, 1 wake-up, 0 ms CPU. The full-view window costs memory only while it's open.

## Check that switching works on your PC

With two accounts of the same provider saved, quit the app (tray: right-click, Quit) and double-click `Verify-Switch.cmd` (add `--provider claude` for Claude). It:
1. Reads real usage for each account.
2. Switches to one account and runs a single tiny prompt ("Reply with exactly: OK") through the official CLI.
3. Simulates that account hitting its limit (in memory only; no quota is burned) and checks that Auto swap moves to the other account and the CLI works there.
4. Switches back and confirms every login is still valid.

That's two tiny requests in total. A report without tokens is saved in `%LOCALAPPDATA%\AccountSwitcher`.

What "seamless" covers today: after a switch, **new** Codex/Claude sessions and new CLI runs use the new account immediately. A session that's already running keeps its account until it's restarted.

## Updating

Double-click `Update.cmd` (or run it from a terminal). It runs once and then exits:
1. It fetches the latest code from GitHub and fast-forwards this folder.
2. It reinstalls the Python requirements if they changed.
3. If the app is running, it closes it cleanly and restarts it in the tray.

```powershell
.\Update.cmd                                           # update the current branch
.\Update.cmd -Branch claude/pensive-brahmagupta-cufhuf # switch to and update another branch
.\Update.cmd -Force                                    # update even with local edits (saved to git stash / a backup branch first)
```

It needs git (`winget install --id Git.Git -e`). A ZIP download is converted into a git checkout on first run; git-ignored files are kept.

## Demo mode and the Recovery lab

`--demo` swaps in sample accounts and shows the Recovery lab. The lab runs the official Claude CLI against local test upstreams and demonstrates AFK: a mid-response quota failure, a switch, then one Continue. It needs Claude Code, and the proxy built with Go 1.26+ (`./experiments/proxy/build.ps1`); add `--simulator` to skip the proxy.

## Development (any OS)

```sh
python -m account_switcher.web --no-browser             # full view only, your real logins (read from ~)
python -m account_switcher.web --simulator --no-browser # sample data
python -m unittest discover -s tests -v
```

Real-account tests use fake login files and a fake provider API. Tray tests use pystray's dummy backend and need `pystray` + `Pillow`. Outside Windows, saved logins are stored unencrypted in owner-only files; that fallback is for development only. Don't expose the dashboard port publicly.

## Layout

- `account_switcher/tray.py`: the app (tray icon, notifications).
- `account_switcher/flyout.py` + `flyout_render.py`: panel and right-click menu (Win32 layered windows + Pillow drawing).
- `account_switcher/live.py`: real accounts (import, usage refresh, switching, auto swap, add/remove).
- `account_switcher/providers.py`: Claude Code / Codex login files and usage APIs.
- `account_switcher/vault.py`: DPAPI-encrypted storage.
- `account_switcher/web.py` + `static/`: controller and full view.
- `account_switcher/core.py`: account model, routing and AFK recovery rules.
- `account_switcher/client.py`, `demo.py`, `proxy_demo.py`: Recovery lab (demo mode).
- `scripts/update.ps1`: the updater.
- `docs/REVIEW.md`: review and priorities.
- `experiments/`, `proxy-fork/`, `codex-vitals-source/`: spikes and vendored upstream sources (see `SOURCE_PROVENANCE.md`).
