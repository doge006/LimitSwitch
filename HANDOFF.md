# Handoff — Account Switcher

Where the project stands and how we work on it, so a fresh session can pick up. Last updated 2026-09-27 (after the cleanup: Recovery lab, web full view and Edge code removed).

## What it is
A Windows tray and macOS menu bar app that manages several Claude Code and Codex accounts:
- live usage;
- two-click swapping;
- Auto swap (move to the account with the most room when a limit hits);
- Auto resume (internally `afk`: a Claude Code session that stops on a limit continues by itself).

Python stdlib plus pystray and Pillow; PyObjC on macOS. Every window is native and drawn with Pillow, except the macOS menu bar panel (still a WKWebView over `static/menu.*`). A loopback API (`web.py`) serves Claude Code's status line and AFK hook, the macOS panel, and a second launch asking the running copy to show its window.

## How the user works
- **Git:** Claude develops on the session's assigned `claude/…` branch (created from `origin/main`), then **creates and merges the PR itself** and deletes the branch, so the public repo has only `main`.
- **Updating:** the user runs `Update.cmd` / `Update.command`, which always follows `main` (`--branch X` overrides) and ends any leftover running copy. Every run is saved to `%LOCALAPPDATA%\AccountSwitcher\update.log`; `app.log` records `started, version <sha>`. **Check both before debugging a "still broken" report**: twice the user was running old code (an old branch; an update that couldn't close the running copy).
- **Testing:** targeted. Unit tests (`python -m unittest discover -s tests`) plus one focused check. The user asked not to run CI for everything: use it for risky Windows-only changes (window/paint/host code, the build), and look at its screenshots before merging UI work.
- **GitHub Actions** are **manual only** (`workflow_dispatch`); the account is near its allowance.
  - `windows.yml` (via `mcp__github__actions_run_trigger`) for Windows behaviour. Its smoke test fails on any error in `app.log`.
  - Avoid `macos.yml` (10× minutes); if needed, run it with the `os` input (e.g. `macos-15`) for one runner.
  - Screenshots are the run's `screenshots-<os>` artifacts (kept 7 days; download with `actions_get` → `download_workflow_run_artifact`). No CI branches.
- **Design:** restrained, premium dark UI matching the old web page's look and motion; resources near zero (event-driven, no polling; animation frames only while something moves). Nothing may spend tokens.
- **Scope:** fix what was asked. No unrequested toggles or features.
- **Security:** never read or print secrets or tokens; no strace.

## Map
- **`tray.py`:** the app entry (`main`), tray icon, notifications. `open_full_view()` (any thread) opens or focuses the native full view; there is no browser fallback. `app_version()` for the log.
- **`flyout.py` / `flyout_render.py`:** Windows panel, compact panel and right-click menu (layered windows + Pillow).
  - Compact panel is always popped out (`Flyout.pinned` property); Hide turns compact off; Expand keeps the full panel popped out.
  - Power needs a second click (`armed == "quit"`), like swapping.
  - Footer switches: Auto swap, Auto resume, Taskbar (short names when they don't fit).
- **`taskbar.py` / `placement.py`:** the taskbar view: a block per provider in the taskbar's free space, owned by the taskbar (no flicker), any display. Hides while an app is full screen on that display (`full_screen_app`: taskbar not topmost, foreground app window covering the display, D3D full screen / presentation mode; tool-window overlays never count).
- **Full view:**
  - `fullview_render.py`: drawing. Tiles (top bar, group heads, cards) are cached; bars and percentages are drawn live over them (`Tile.live`) so they animate cheaply. Anti-aliased shapes from cached masks.
  - `fullview.py`: behaviour, independent of the platform: hover, clicks, menus, name editing, the renewal-date calendar, toasts, scroll, and `Motion` (the web page's transitions; frames only while something moves).
  - Hosts: `fullview_win.py` (Win32, on the tray thread; timer ids in `TIMERS`, unit-tested), `fullview_mac.py` (NSView; **not yet run on a real Mac**), `fullview_tk.py` (Linux; Tk on its own thread, all Tk objects freed there).
- **`win_window.py`:** our taskbar identity (AUMID) for the full view window.
- **`web.py`:** the `Controller` (state snapshot, actions) and the loopback API. Snapshot keys: `accounts`, `autoSwap`, `afk`, `compact`, `taskbar*`, `nameMode`, `busy`, `log`, `signingIn`.
- **`live.py`:** real accounts, usage polling and the meta store. Claude usage comes mostly from Claude Code's status line (`statusline.py`); within a window usage never goes down (stale repeats ignored) and only changing reports count as live. A Claude limit hit marks the account used up when the API can't answer.
- **`demo.py` / `core.py`:** sample accounts for `--demo` (CI, screenshots); the account model and router.
- **`macos_app.py`:** the menu bar app.
- **`scripts/installer.py`:** install/update for Windows and macOS.
- **`.github/win_smoke.py`:** Windows CI: full view (identity, one window, reopen at the same size, second launch, no browser, memory), taskbar blocks, demo full view with hover/menu/scroll screenshots.

## Open
- **macOS:** the native full view hasn't run on a Mac. Next Mac work (asked for): a full view without a title bar, and a menu bar usage view like the taskbar blocks (task in progress list).
- **Next (asked for):** easy (portable) install, "Check for updates" in Settings plus a check at launch, and a "Launch with Windows / macOS" setting.
- **Taskbar:** the right-monitor option is untested on real two-monitor hardware.
- **Mac rate limit:** root cause still unknown; waiting on the user's `grep "rate limited" … app.log | tail -3`.
