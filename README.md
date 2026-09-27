# Account Switcher

A Windows tray / macOS menu bar app that shows every Claude Code and Codex usage limit at a glance, switches accounts in one click, and can switch automatically when the account in use hits a limit.

## Install and update (Windows and macOS)

One installer for both; it detects the OS. Run it again at any time to update.

- **Windows:** double-click `Install.cmd` (or `Update.cmd`, which does the same).
- **macOS:** in Terminal, run `bash Install.command` in this folder (or `bash Update.command`).
  - Double-clicking works only if the folder came from `git clone`. A downloaded ZIP is flagged by macOS, and it refuses to open the script ("can't verify it's free of malware"). To double-click anyway, clear the flag once with `xattr -dr com.apple.quarantine <folder>`, or use **System Settings → Privacy & Security → Open Anyway**.
  - The **Account Switcher** app the installer creates opens without that warning, because it's made on your Mac.

Each run:
1. Makes sure Python 3.10+ and git are there. Windows installs them with winget; macOS asks for Apple's command line tools.
2. Updates this folder from GitHub. Local edits and local-only commits are never lost: without `--force` it stops and says why, and with `--force` it saves them to `git stash` or a backup branch first.
3. Sets up a private Python environment (`.venv`) with this OS's requirements; they're only reinstalled when they change.
4. Puts the app where you'd expect it: a Start menu shortcut on Windows, **Account Switcher** in Applications on macOS (`/Applications`, or `~/Applications` if that isn't writable). The app registers itself to start at sign-in.
5. Restarts the app on the new version (or starts it on a first install).

Options: `--branch NAME` (switch to and update another branch), `--force`, `--no-launch`.

A first install from nothing: `git clone https://github.com/doge006/Account-Switcher.git`, then run the installer in that folder.

## macOS

- **Menu bar:** the tray becomes a menu bar icon.
  - Click it for the panel, a native popover that follows light and dark mode.
  - Drag the panel away from the menu bar and it stays open as a floating window.
  - Right-click (or Control-click) for the menu; **Full View…** opens the dashboard in its own window.
  - There's no Dock icon.
- **Claude Code** keeps its login in the macOS Keychain ("Claude Code-credentials"). The app switches that item, and a running Claude Code picks it up on its next request.
- **Codex** works exactly as on Windows (the local router, `~/.codex/auth.json`).
- **Saved logins** are encrypted with a random key kept in your login Keychain, under `~/Library/Application Support/AccountSwitcher`.
- **Start at login:** a LaunchAgent (`~/Library/LaunchAgents/com.accountswitcher.app.plist`).

## Run it by hand (Windows)

```powershell
.\Launch.cmd            # starts the tray and opens the full view
.\Launch.cmd --quiet    # starts in the tray only
.\Launch.cmd --demo     # sample accounts + the Recovery lab, no real logins touched
```

**Start menu shortcut:** the installer adds it. `Add-Shortcut.cmd -Desktop` also puts one on the desktop, and `-Remove` takes them away. The shortcut starts the app without a console window. If the app is already running, it opens the full view instead.

## Your accounts

- **Adding accounts:** whatever Claude Code / Codex login is active on this PC is picked up automatically. Signing in to another account (`claude auth login`, `codex login`, or the apps) adds it too. **Add account** (in the full view or the tray menu) runs the official sign-in in a separate window and an isolated folder, so the login you're using isn't touched.
- **Switching:** click an account and every session moves to it, including sessions that are already open. Nothing needs restarting.
  - *Claude Code:* the app saves the outgoing account's newest tokens and writes the chosen login into `~/.claude/.credentials.json` + `~/.claude.json`. A running Claude Code notices and uses it on its next request.
  - *Codex:* the chosen login is written into `~/.codex/auth.json` (so new windows and Codex's `/status` show it), and while the app runs, Codex sends its requests through the app (a local router on `127.0.0.1`), which adds the chosen account's login. So a switch also applies to sessions that are already open, on their next request.
  - *On quit:* the app puts `~/.codex/config.toml` back exactly as it was, so Codex works without the app, on the chosen account.
- **What the app changes in `~/.codex/config.toml` while it runs** (every line is tagged `# account-switcher` and removed again on quit):
  - `openai_base_url` points at the router;
  - `enable_request_compression = false`, so the router can read requests;
  - `daemon_auto_start = false`. Codex's shared background server opens a console window for every command on Windows ([openai/codex#44768](https://github.com/openai/codex/issues/44768), [#48074](https://github.com/openai/codex/issues/48074)). Sessions also attach to it whenever it's running, and it keeps the settings it started with, so its sessions would bypass the router. The app stops a running one as soon as no session has been active for 90 seconds; after that, each Codex session runs in its own terminal, goes through the router, and nothing flashes.
- **Start with Windows:** on by default, since Codex's requests go through the app.
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
  - Claude: its profile reports only when the subscription started and whether it's active or cancelled. The renewal is estimated as the next monthly anniversary and shown with a ~ (e.g. "Renews ~Oct 14").
  - Both are checked at most once a day. When nothing is reported, click **Set renewal date** on the card; a date you enter always wins.
  - The names of the fields these endpoints return (never their values) are kept in `subscription-fields.json`, to help match the detection to real responses.
- **Usage limit resets:** banked resets are shown for Codex, which reports them. Claude's usage response doesn't include its free resets (they appear only in Claude's settings), so none are shown for Claude.
- **Auto swap:** each account is used to 100%; then the app moves to the account with the most room, and the thread carries on with everything it had.
  - *Codex:* the request that hit the limit is sent again on the next account, so the session never sees the error. If ChatGPT's response headers already showed the account used up, a new turn simply starts on the next account.
  - *Claude:* Claude Code shows its limit message. The hook below switches accounts right away, so your next message uses the new account; with AFK on, it also continues by itself.
  - *Claude threads:* nothing in them is tied to an account, so they carry over whole.
  - *Codex threads:* ChatGPT encrypts two things for the account that made them, which another account can't read. The app makes no extra requests for this:
    - *Hidden reasoning:* another account receives the plain-text summary ChatGPT returned with it.
    - *Compaction checkpoints* (the summary Codex keeps instead of old history): the thread stays on the checkpoint's account while that account has quota. Once it's used up, the thread moves on without that older summary; the recent conversation is kept.
- **AFK:** while AFK is on, a Claude Code session that stops on a usage limit continues by itself, with nobody typing.
  - The app adds a `StopFailure` hook to `~/.claude/settings.json` while Auto swap or AFK is on. Only its own entry is added, and it's removed when both are off.
  - When the hook fires, the app switches to an account with room (Auto swap) and Claude Code is told to continue where it left off.
  - If no account has room, it waits for the earliest reset (up to 6 hours) and then continues.
  - Codex needs no hook: its requests are retried on the next account automatically.
- **Storage:** saved logins are encrypted with Windows DPAPI (tied to your Windows user) under `%LOCALAPPDATA%\AccountSwitcher`. Nothing is sent anywhere except the providers' own usage and token endpoints.
- **Token ownership:** each account should be managed from here only. If the same account is also signed in elsewhere and refreshes its token there, this copy expires and shows "Sign in again". The in-use account's token is never refreshed by this app; that stays with Claude Code / Codex.

Check the providers' terms for using several subscriptions this way; that's your call.

## Tray

- **Left-click the icon:** a compact panel opens above it. It lists every account by email with its plan and subscription status ("Renews 18d" / "Ends 3d"), plus 5h / 1w / per-model headroom with the time until each resets. Click an account to switch; you'll see "Switching…" until it lands. Clicking the icon again closes the panel, pinned or not.
- **Hidden icons (^):** if the icon lives in Windows' hidden-icons popup, left-clicking it closes that popup (the app sends it one Esc, only when that popup is the active window) and the panel takes its place. Right-clicking leaves it open, like Steam's menu.
- **Motion:** hovers fade, switches slide, the "in use" marker cross-fades on a switch, and bars glide to new values. Animation frames are drawn only while something moves.
- **The panel:**
  - **Pop out** (next to **Full view ›**) pins the panel: it stays open and you can drag it by its header. Click it again to put it back.
  - **Full view ›** opens the dashboard in a borderless Edge/Chrome app window.
  - Esc or clicking elsewhere closes an unpinned panel.
- **Right-click:** a menu in the same style: Open panel, Full view, Auto swap, AFK and Quit. Toggling Auto swap or AFK keeps the menu open.
- **Hover:** the tooltip shows the account in use per provider and what's left.
- **The icon's dot:** green, amber or red for the tightest limit in use.
- **Launching again:** opens the running copy's full view instead of starting a second copy.
- **Quit** stops everything the app started and puts the Codex and Claude Code settings back. The app starts with Windows (Codex routing depends on it); to turn that off, delete the `AccountSwitcher` entry under Task Manager → Startup apps.

Resource use: one Python process that sleeps until an account is due for a check or something changes. The panel and menu are native windows drawn with Pillow; they exist only while open. Measured idle over 60 s in real-account mode (Linux, virtual display): 32 MB, 1 wake-up, 0 ms CPU. The full-view window costs memory only while it's open.

## Check that switching works on your PC

With two accounts of the same provider saved, quit the app (tray: right-click, Quit) and double-click `Verify-Switch.cmd` (add `--provider claude` for Claude). It:
1. Reads real usage for each account.
2. Switches to one account and runs a single tiny prompt ("Reply with exactly: OK") through the official CLI.
3. Simulates that account hitting its limit (in memory only; no quota is burned) and checks that Auto swap moves to the other account and the CLI works there.
4. Switches back and confirms every login is still valid.

That's two tiny requests in total.
- **For the cleanest result:** pause any running Codex/Claude session and close the ChatGPT app first, so nothing writes the old login back mid-test. The script warns if one is open.
- **"Model not supported":** if your CLI's default model isn't offered on your plan (for example a model only available with an API key), the prompt comes back that way. That still proves the switch: you were signed in and reached the provider as that account, and the report says so. Add `--model <name>` to run the prompt with a model your plan offers. A report without tokens is saved in `%LOCALAPPDATA%\AccountSwitcher`.

`Verify-Switch.cmd` checks the login-file switch (what Codex uses while the app is closed, and what Claude Code always uses). With the app running, Codex switching goes through the router instead. That path is covered by `tests/test_routing.py` and was checked with the real Codex CLI.

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
- `account_switcher/live.py`: real accounts (import, usage refresh, switching, auto swap, AFK decisions, add/remove).
- `account_switcher/codex_proxy.py` + `codex_config.py`: the Codex router and the config lines that point Codex at it.
- `account_switcher/claude_hooks.py` + `afk_hook.py`: the Claude Code AFK hook.
- `account_switcher/integrations.py`: sets all of that up while the app runs and undoes it on quit.
- `account_switcher/providers.py`: Claude Code / Codex login files and usage APIs.
- `account_switcher/vault.py`: DPAPI-encrypted storage.
- `account_switcher/web.py` + `static/`: controller and full view.
- `account_switcher/core.py`: account model, routing and AFK recovery rules.
- `account_switcher/client.py`, `demo.py`, `proxy_demo.py`: Recovery lab (demo mode).
- `scripts/installer.py` (+ `Install.cmd` / `Install.command`): the installer and updater.
- `account_switcher/macos_app.py` + `static/menu.*`: the macOS menu bar app and its panel.
- `account_switcher/keychain.py`: macOS Keychain access.
- `experiments/`: spikes and the Recovery lab's proxy harness (upstream sources: `SOURCE_PROVENANCE.md`).
