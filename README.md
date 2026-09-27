# Account Switcher

A Windows tray / macOS menu bar app that shows every Claude Code and Codex usage limit at a glance, switches accounts in one click, and can switch automatically when the account in use hits a limit.

## Install (Windows)

Either way installs the same portable copy: `Account Switcher.exe`, the app and its own Python. Nothing else is needed (no Python, no git), and nothing is installed system-wide.

- **One line:** in PowerShell, run
  ```powershell
  irm https://raw.githubusercontent.com/doge006/Account-Switcher/main/install.ps1 | iex
  ```
  It asks where to install (press Enter for `%LOCALAPPDATA%\Programs\Account Switcher`, or where you installed it last time), downloads the latest release there and starts it. Run it again to update.
- **Download:** get `AccountSwitcher-windows.zip` from the [latest release](https://github.com/doge006/Account-Switcher/releases/latest), unzip it anywhere, and double-click `Account Switcher.exe`.

On its first run it adds itself to the Start menu and starts with Windows (switch that off in the full view's Settings). Your accounts and settings live in `%LOCALAPPDATA%\AccountSwitcher`, not in the app's folder.

**Updates:** the app checks GitHub Releases once at launch and tells you when a new version is out. Settings → **Update to …** downloads it, closes the app, puts the new files in place and starts it again. **Check for updates** checks now.

## Install and update from source (Windows and macOS)

For development, or macOS (no portable build yet). One installer for both; it detects the OS. Run it again at any time to update.

- **Windows:** double-click `Install.cmd` (or `Update.cmd`, which does the same).
- **macOS:** in Terminal, run `bash Install.command` in this folder (or `bash Update.command`).
  - Double-clicking works only if the folder came from `git clone`. A downloaded ZIP is flagged by macOS, and it refuses to open the script ("can't verify it's free of malware"). To double-click anyway, clear the flag once with `xattr -dr com.apple.quarantine <folder>`, or use **System Settings → Privacy & Security → Open Anyway**.
  - The **Account Switcher** app the installer creates opens without that warning, because it's made on your Mac.

Each run:
1. Makes sure Python 3.10+ and git are there. Windows installs them with winget; macOS asks for Apple's command line tools.
2. Updates this folder from GitHub (`main`). Local edits and local-only commits are never lost: without `--force` it stops and says why, and with `--force` it saves them to `git stash` or a backup branch first.
3. Sets up a private Python environment (`.venv`) with this OS's requirements; they're only reinstalled when they change.
4. Puts the app where you'd expect it: a Start menu shortcut on Windows, **Account Switcher** in Applications on macOS (`/Applications`, or `~/Applications` if that isn't writable).
5. Closes any running copy and starts the new version. Its output is also saved to `update.log` next to `app.log`.

Options: `--branch NAME` (follow another branch), `--force`, `--no-launch`. A copy from source also offers updates in Settings; there, **Update** runs this installer.

A first install from nothing: `git clone https://github.com/doge006/Account-Switcher.git`, then run the installer in that folder.

## Publishing a release

Bump `VERSION` in `account_switcher/version.py`, merge, then run the **Release** workflow (Actions tab). It builds the portable zip (`scripts/build_portable.ps1`), installs it with `install.ps1` and checks that it runs, then publishes release `v<VERSION>`. Users get it on their next launch.

## Performance

`scripts/measure.py` measures the running app: memory (working set and private), CPU share and threads over a stretch of time.

```powershell
.venv\Scripts\python -m pip install psutil     # once (a portable copy: runtime\python.exe -m pip ...)
.venv\Scripts\python scripts\measure.py        # 60 s; --seconds N
```

Measure the tray alone, with the panel open, and with the full view open, a few times each, and quote the typical number with the machine it ran on.

## macOS

- **Menu bar:** the tray becomes a menu bar icon.
  - Click it for the panel, a native popover that follows light and dark mode.
  - Drag the panel away from the menu bar and it stays open as a floating window.
  - Right-click (or Control-click) for the menu; **Full View…** opens the full view in its own native window.
  - There's no Dock icon.
- **Claude Code** keeps its login in the macOS Keychain ("Claude Code-credentials"). The app switches that item, and a running Claude Code picks it up on its next request.
- **Codex** works exactly as on Windows (the local router, `~/.codex/auth.json`).
- **Saved logins** are encrypted with a random key kept in your login Keychain, under `~/Library/Application Support/AccountSwitcher`.
- **Start at login:** a LaunchAgent (`~/Library/LaunchAgents/com.accountswitcher.app.plist`).

## Run it by hand (Windows)

```powershell
.\Launch.cmd            # starts the tray and opens the full view
.\Launch.cmd --quiet    # starts in the tray only
.\Launch.cmd --demo     # sample accounts, no real logins touched
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
- **Start with Windows:** on by default, since Codex's requests go through the app. Switch it off in the full view's Settings (**Launch with Windows**).
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
  - *Claude:* Claude Code shows its limit message. The hook below switches accounts right away, so your next message uses the new account; with Auto resume on, it also continues by itself.
  - *Claude threads:* nothing in them is tied to an account, so they carry over whole.
  - *Codex threads:* ChatGPT encrypts two things for the account that made them, which another account can't read. The app makes no extra requests for this:
    - *Hidden reasoning:* another account receives the plain-text summary ChatGPT returned with it.
    - *Compaction checkpoints* (the summary Codex keeps instead of old history): the thread stays on the checkpoint's account while that account has quota. Once it's used up, the thread moves on without that older summary; the recent conversation is kept.
- **Auto resume:** while it's on, a Claude Code session that stops on a usage limit continues by itself, with nobody typing.
  - The app adds a `StopFailure` hook to `~/.claude/settings.json` while Auto swap or Auto resume is on. Only its own entry is added, and it's removed when both are off.
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
  - **Compact** (the button next to the power button) shrinks it to the accounts in use. The compact panel is always popped out; **Expand** brings the full panel back (still popped out) and **Hide** puts it away, so the tray icon opens the normal panel next time.
  - **Power** quits, after a second click to confirm.
  - **Full view ›** opens the full view: a native window of the app's own (no browser), drawn like the panel.
  - Esc or clicking elsewhere closes an unpinned panel.
- **Right-click:** a menu in the same style: Open panel, Full view, Auto swap, Auto resume and Quit. Toggling Auto swap or Auto resume keeps the menu open.
- **Hover:** the tooltip shows the account in use per provider and what's left.
- **The icon's dot:** green, amber or red for the tightest limit in use.
- **Launching again:** opens the running copy's full view instead of starting a second copy.
- **Quit** stops everything the app started and puts the Codex and Claude Code settings back. The app starts with Windows (Codex routing depends on it); Settings → **Launch with Windows** turns that off.

Resource use: one Python process that sleeps until an account is due for a check or something changes. The panel and menu are native windows drawn with Pillow; they exist only while open. Measured idle over 60 s in real-account mode (Linux, virtual display): 32 MB, 1 wake-up, 0 ms CPU. The full-view window costs memory only while it's open.

## Demo mode

`--demo` shows sample accounts instead of your real logins, with no network use. It's for screenshots and the Windows smoke test.

## Development (any OS)

```sh
python -m account_switcher.tray --demo   # the app with sample accounts (Linux: needs python3-tk for the full view)
python -m unittest discover -s tests -v
```

Real-account tests use fake login files and a fake provider API. Tray tests use pystray's dummy backend and need `pystray` + `Pillow`. Outside Windows and macOS, saved logins are stored unencrypted in owner-only files; that fallback is for development only. The local API listens on 127.0.0.1 only and needs a per-run token.

## Layout

- `account_switcher/tray.py`: the app (tray icon, notifications, startup).
- `account_switcher/flyout.py` + `flyout_render.py`: the Windows panel and right-click menu (layered windows + Pillow drawing).
- `account_switcher/taskbar.py` + `placement.py`: the Windows taskbar view.
- `account_switcher/fullview.py` + `fullview_render.py`: the full view (behaviour and Pillow drawing), shown by `fullview_win.py` (Win32), `fullview_mac.py` (AppKit) and `fullview_tk.py` (Linux, Tk).
- `account_switcher/live.py`: real accounts (import, usage refresh, switching, auto swap, Auto resume decisions, add/remove).
- `account_switcher/providers.py`: Claude Code / Codex login files and usage APIs.
- `account_switcher/codex_proxy.py` + `codex_config.py`: the Codex router and the config lines that point Codex at it.
- `account_switcher/claude_hooks.py`, `afk_hook.py`, `statusline.py`: the Claude Code hook (Auto resume) and status line.
- `account_switcher/integrations.py`: sets all of that up while the app runs and undoes it on quit.
- `account_switcher/web.py`: the controller and the local API (status line, hook, the macOS panel, a second launch).
- `account_switcher/core.py` + `demo.py`: the account model and routing; sample accounts for `--demo`.
- `account_switcher/vault.py`, `keychain.py`: encrypted storage (DPAPI on Windows, the Keychain on macOS).
- `account_switcher/macos_app.py` + `static/menu.*`: the macOS menu bar app and its panel.
- `account_switcher/version.py` + `updates.py`: the version, and update checks / installs from GitHub Releases.
- `scripts/installer.py` (+ `Install.cmd` / `Install.command`): install and update from source.
- `scripts/build_portable.ps1` + `win_launcher.c`, `install.ps1`: the portable Windows release and its one-line installer.

## Credits

`account_switcher/providers.py` follows the usage clients of Codex Vitals (https://github.com/Joowonoil/Codex-Vitals, MIT; see `THIRD-PARTY-NOTICES.txt`).
