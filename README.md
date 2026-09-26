# Account Switcher

A Windows tray app that shows every Claude Code and Codex usage limit at a glance, swaps accounts, and (with AFK mode) continues an interrupted Claude session after a quota failure.

**Accounts and usage are still synthetic.** Real sign-in, encrypted credential storage and live usage fetching are the next milestones. See `docs/REVIEW.md`.

## Run it (Windows)

Needs Python 3.12+.

```powershell
python -m pip install -r requirements-native.txt
.\Launch.cmd            # starts the tray and opens the dashboard
.\Launch.cmd --quiet    # starts in the tray only
```

- **Left-click the tray icon:** opens the dashboard, a borderless Edge/Chrome app window (falls back to your default browser).
- **Right-click:** one-click account swaps with 5-hour and weekly headroom, plus Auto swap, AFK mode and Quit.
- **Hover:** shows the account in use per provider and how much is left.
- **The icon's dot:** green, amber or red for the tightest limit in use.
- **Failover:** a Windows notification says what switched and why.
- **Launching again:** opens the running copy's dashboard instead of starting a second one.
- **Quit** (tray menu or dashboard) stops everything the app started. Nothing runs at sign-in.

Resource use: one Python process with no timers or polling. It sleeps until something changes. Measured idle over 30 s (Linux, virtual display): about 31 MB, 3 threads, 0 CPU, 0 wake-ups. The dashboard window costs memory only while it's open. The Go proxy and Claude CLI run only between Run and Stop in the Recovery lab.

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

It needs git (`winget install --id Git.Git -e`). A ZIP download is converted into a git checkout on first run; git-ignored files such as the built proxy are kept.

## Recovery lab (optional)

The dashboard's Recovery lab runs the official Claude CLI against local test upstreams to demonstrate AFK recovery. It needs Claude Code installed and the proxy built with Go 1.26+:

```powershell
./experiments/proxy/build.ps1
```

`--simulator` (on `Launch.cmd` or the dev server) skips the compiled proxy. The fixture uses dummy accounts, disables coding tools, and never loads your real credentials.

## Development (any OS)

```sh
python -m account_switcher.web --simulator --no-browser   # dashboard only, prints a private URL
python -m unittest discover -s tests -v
```

Tray tests use pystray's dummy backend and need `pystray` + `Pillow`; they skip if those are missing. Tests needing the Claude CLI or the built proxy also skip when absent. Don't expose the dashboard port publicly.

## Layout

- `account_switcher/tray.py`: the app (tray icon, menu, notifications).
- `account_switcher/web.py` + `static/`: controller and dashboard.
- `account_switcher/core.py`: routing and AFK recovery rules.
- `account_switcher/client.py`, `demo.py`, `proxy_demo.py`: Recovery lab plumbing.
- `scripts/update.ps1`: the updater.
- `docs/REVIEW.md`: latest review and priorities.
- `ACCOUNT_SWITCHER_TECHNICAL_PLAN.md`: original plan.
- `experiments/`, `proxy-fork/`, `codex-vitals-source/`: compatibility spikes and vendored upstream sources. See `SOURCE_PROVENANCE.md`.
