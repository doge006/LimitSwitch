# Account Switcher

Local Claude/Codex account-manager prototype with a Windows tray dashboard and a shared authenticated Web UI. Native account rows use actual Codex Vitals source. Dark #1e1e1e surfaces, provider logos, an app icon, rounded buttons and finite motion are included.

**All accounts and usage are synthetic.** Real login, encrypted token storage, actual quota fetching, live failover, interactive CLI attachment and VS Code/desktop integration are unfinished. Codex currently demonstrates selection only.

## Windows

Requires Python 3.13 with Tk.

```powershell
python -m pip install -r requirements-native.txt
python -m account_switcher.native
```

`Launch.cmd` uses the original machine's Python 3.13 installation. The module command above works with your selected Python. `--tray` starts hidden; `--port 8765` selects a fixed port. Open Web UI supplies the private per-launch URL. Close hides to tray; Exit or Web Shut down closes the host and owned processes. Nothing is registered at Windows sign-in.

Hover the tray icon for selected-account quota status; click it for the compact account panel. Recovery controls are under Session tools. Motion respects Windows animation settings and stops when settled.

For the default recovery experiment, install Claude Code and build the proxy using Go 1.26 or newer:

```powershell
./experiments/proxy/build.ps1
```

The proxy and client start only on Run and stop on Stop. The fixture uses dummy accounts and local upstreams, with coding tools disabled. It never loads existing client credentials. `--simulator` bypasses the compiled proxy; running its recovery experiment still requires the Claude CLI.

## Updating from GitHub

`Update.cmd` pulls the latest code into this folder (it needs git; a ZIP download is converted into a git checkout on first run). It never overwrites local edits unless you pass `-Force`, which saves them to a stash or backup branch first.

```powershell
.\Update.cmd                                           # update once, current branch
.\Update.cmd -Branch claude/pensive-brahmagupta-cufhuf # switch to and track a branch
.\Update.cmd -Watch -Launch native                     # dev loop: check every 60 s, restart the app on new commits
.\Update.cmd -Watch -Launch web -Simulator -Interval 30
```

Restarts are clean: the script asks the running app to shut down over its private local URL (stored in the git-ignored `.runtime/` folder while the app runs), so the proxy and Claude processes it owns stop too. Nothing is installed or scheduled.

## Cloud sessions / Linux

This repository contains both modified upstream sources as ordinary folders. No submodules or private machine paths are required for core/Web development.

For full test discovery, install Tk and Pillow (on Debian/Ubuntu: `apt-get install python3-tk`, then `python -m pip install Pillow==12.2.0`). A virtual environment is recommended. Native tray interaction must be tested on Windows.

```sh
python -m account_switcher.web --simulator --no-browser
python -m unittest discover -s tests -v
```

The Web server prints a private loopback URL. Use the cloud environment's authenticated port-forwarding facility if available; do not expose the control port publicly. Tests requiring Claude, the built Windows proxy, or a graphical display skip when prerequisites are absent. Pure core tests need only Python:

```sh
python -m unittest discover -s tests -p test_core.py -v
```

The live Windows tray and installed-client workflows remain Windows-specific. Cloud sessions can edit source and exercise portable logic/Web behavior.

## Verification and boundaries

The latest UI pass passed 25 tests, native smoke checks and programmatic popup reversal/account selection. Native screenshots were reviewed. Full live-account behavior has never been verified.

The Claude fixture demonstrates terminal quota failure followed by opt-in Continue within the same session. It does not guarantee word-exact continuation, and Claude may omit incomplete assistant output. The reserve account is deliberately failed and restored in the fixture to let native retries settle.

A 10-second idle sample with both native views open measured 58.82 MiB working set, 32.82 MiB private memory and 0 CPU seconds. Browser/client/proxy costs are separate. GPU use is unmeasured. The original 30 MB target remains unmet.

See `docs/REVIEW.md` for the latest review and priorities.

## Source map

- `account_switcher/`: controller, clients, native shell and Web assets.
- `tests/`: routing, recovery, Web lifecycle and native controls.
- `experiments/`: reproducible compatibility scripts and result summaries; raw transcripts are regenerated locally.
- `SOURCE_PROVENANCE.md`: pinned upstream versions and local changes. Original licenses remain in each source folder.
- `IMPLEMENTATION_STATUS.md`: evidence and outstanding implementation work.
- `ACCOUNT_SWITCHER_TECHNICAL_PLAN.md`: original technical plan; current verified status takes precedence over its proposed work.

Upstream Vitals and proxy credential-management code has not been integrated. Do not run the upstream application entry points as our launcher. Build caches, executables, toolchains, temporary credentials and local handoffs are excluded from version control.
