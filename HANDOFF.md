# Handoff — Account Switcher

Where the project stands and how we work on it, so a fresh session can pick up. Last updated 2026-09-27 (native full view on all platforms; taskbar blocks hide for full-screen apps).

## What it is
A Windows tray and macOS menu bar app that manages several Claude Code and Codex accounts:
- live usage;
- two-click swapping;
- Auto swap;
- AFK continuation.

It is Python stdlib plus pystray and Pillow on Windows, and PyObjC on macOS. A loopback web dashboard ("full view") opens in an Edge `--app` window on Windows and a WKWebView on macOS.

## How the user works
- **Git:** Claude develops on the session's assigned `claude/…` branch, reset to `origin/main` after each merge, then **creates and merges the PR itself**. The user updates locally with `Update.cmd` / `Update.command`.
- **Testing:** keep it targeted. Run the unit tests (`python -m unittest discover -s tests`), plus one focused check. Don't test at length.
- **GitHub Actions** are **manual only** (`workflow_dispatch`) to save minutes; the account hit 90% of its allowance.
  - Run `windows.yml` by hand only for Windows-only behaviour (via `mcp__github__actions_run_trigger`).
  - Avoid `macos.yml` (Mac minutes cost 10×). When a Mac check is needed, run it with the `os` input (e.g. `macos-15`) for one runner instead of three.
  - Screenshots from CI go to the `ci-shots/<os>` branches.
- **Design:** restrained, premium dark UI; resources near zero (event-driven, no polling). Nothing may spend tokens, including extra Codex tokens.
- **Scope:** fix the bug that was asked about. Don't add toggles or features that weren't requested.
- **Security:** never read or print secrets or tokens, and don't use strace.

## Map
- **`account_switcher/tray.py`:** the Windows host.
  - `full_view_size()` / `fullview.window_size()`: 920 tall (95% of shorter screens), half the width; the window then shrinks to its content so there's no empty space at the bottom.
  - `open_full_view()` opens the native full view (`full_view.post_show()`, any thread); `open_dashboard()` (Edge) is only the fallback.
- **Native full view (no browser, since the "full view blink" rounds):**
  - `fullview_render.py`: Pillow drawing, the web look (cards, settings menu, add menu, renewal-date calendar, toasts). Tiles (top bar, group heads, cards) are drawn once and cached; anti-aliased shapes come from cached masks, not a 2x canvas.
  - `fullview.py`: platform-independent behaviour (hover, clicks, name editing, menus, toasts, scroll, timers). Host interface: invalidate, set_timer/kill_timer/has_timer, set_cursor, clipboard.
  - Hosts: `fullview_win.py` (Win32 window on the tray thread, SetDIBitsToDevice, our AUMID via `win_window.set_identity`), `fullview_mac.py` (NSView in the existing NSWindow; **not yet run on a Mac**), `fullview_tk.py` (Linux; Tk on its own thread, all Tk objects freed on that thread).
  - Measured on Windows CI: whole app 62 MB working set with the full view open; the window adds ~8 MB; cached frame ~5 ms, hover ~12 ms. Edge is gone.
  - Not in the native view: the Recovery lab (demo-only dev tool), still in the web page (`python -m account_switcher.web`).
- **`win_window.py`:** taskbar identity helpers (`set_identity`, `get_identity`), plus the old Edge-window code used by the fallback.
- **`flyout.py` / `flyout_render.py`:** the Win32 layered popups (tray panel, menu, compact mode) and their Pillow drawing.
  - The `Popup` base has `modal`, `ex_style` and `owner`.
  - The panel has `only=<provider>` when opened from a taskbar block.
  - The footer has Auto swap, AFK and Taskbar switches.
- **`taskbar.py`:** the taskbar view. There is one block per provider, drawn in the taskbar's free space (Claude from the left, Codex from the right).
  - **Gaps:** found from the taskbar's buttons through UI Automation, using raw COM vtable calls.
  - **Placement:** `placement.free_gaps` and `place_blocks`.
  - **Z-order:** blocks are **owned by the taskbar**, which is the flicker fix.
  - **Full screen:** blocks hide while an app is full screen on their display (`follow_taskbar` + `full_screen_app`): the taskbar losing `WS_EX_TOPMOST`, the foreground app window covering the display (borderless games, video, F11), or D3D full screen / presentation mode. Tool windows (screenshot overlays) never count.
  - **Displays:** chosen with `taskbarDisplay` (main, left, right). Settings live in the meta.
- **`web.py`:** the Controller and the HTTP API.
  - **Actions:** `compact`, `taskbar {on, display}`, `names {on}`, `rename {id, name}`, and others.
  - **Snapshot keys:** `nameMode`, `label`, `taskbar*`, `compact`.
  - **Name mode:** replaces `name` server-side, so every surface follows. Unnamed accounts show as "Claude 1".
- **`live.py`:** real accounts, usage polling (gentle on Claude's API, which rate-limits) and the meta store.
  - **Meta:** all saved keys now survive a restart (fixed in #44).
  - **Live Claude usage:** comes from Claude Code's statusLine JSON (`statusline.py` → `/api/statusline`).
- **Static front ends:**
  - `static/index.html`, `app.js` and `style.css` are the full view. The Settings dropdown has Auto swap, AFK, Name mode, and Taskbar view with its display. In name mode the card shows name, then email (`d**********@gmail.com`, click to reveal), then plan. Credits and resets share one row.
  - `static/menu.*` is the macOS panel (still a WKWebView, created at launch: the next resource win on macOS is drawing it natively too).
- **`macos_app.py`:** the macOS menu bar app.
- **`.github/win_smoke.py`:** the Windows CI smoke test. It covers the full view identity and size, the taskbar blocks (placement, ownership, swap animation, full screen, click to open a per-provider panel) and the on/off switch.

## Recently done (PRs #41–#48)
- **Taskbar view:**
  - Flicker fix: blocks owned by the taskbar.
  - Any display.
  - Per-provider panel on click.
  - Full email, with resets shown after the plan.
  - Darker backing plate.
  - No re-animation from screenshots.
- **Settings menu:** it replaces "Automation" in the full view.
- **Settings persistence:** all settings are now remembered.
- **Actions:** set to manual only.
- **Repo cleanup:** removed the vendored sources, 1,810 files down to 84.
- **Name mode** for screen sharing.
- **Full view window:**
  - Opens at 920 tall.
  - Has no bottom gap.
  - Fixed the open/close/open blink.

- **Full view blink, root cause (traced on CI with `experiments/win_trace.py`):** Edge creates the app window hidden, titled `127.0.0.1_/` (not host:port), and shows it ~0.3 s later. We only matched it after the page loaded, then hid, moved and re-showed it: seen as "one closes, another opens". `brand_full_view` now catches the new window (not in `before`) while still hidden and never hides it. Edge ignores moves while hidden, so it is fitted again within ~1 frame of showing.
- **One full view:** opening it again (tray, flyout, Start menu) brings the open window to the front (`win_window.focus_full_view`) instead of a second Edge window. A second launch hands off to the running copy (`server.show` → `Tray.open_full_view`, with `AllowSetForegroundWindow`), so the DPI-aware process sizes it the same as the tray does.

## Open / to verify with the user
- **Native full view:** verify on the user's PC (scaling, fonts, feel). macOS host (`fullview_mac.py`) has not run on a Mac yet: one `macos.yml` run with `os: macos-15` would check it. Linux host was run under Xvfb only.
- **Taskbar blocks:**
  - Check the "moved to left monitor" report; it should be gone now that there is no fallback to another display.
  - Check the right-monitor option on a real two-monitor setup (CI has one screen).
- **Name mode email:** the user asked for the first *and* last character to stay visible, but gave the example `d**********@gmail.com`. The example is what's implemented; it's a one-line change in `app.js` `redact()`.
- **Mac rate limit:** the root cause is still unknown. We were waiting on the user's `grep "rate limited" … app.log | tail -3`.
- **Old CI screenshot branches:** the `ci-shots/*` branches could be deleted, but the user hasn't confirmed.
