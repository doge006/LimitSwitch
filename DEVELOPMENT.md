# Developing LimitSwitch

## Install and update from source (Windows and macOS)

One installer for both; it detects the OS. Run it again at any time to update.

- **Windows:** double-click `Install.cmd` (or `Update.cmd`, which does the same).
- **macOS:** in Terminal, run `bash Install.command` in this folder (or `bash Update.command`).
  - Double-clicking works only if the folder came from `git clone`. A downloaded ZIP is flagged by macOS, and it refuses to open the script ("can't verify it's free of malware"). To double-click anyway, clear the flag once with `xattr -dr com.apple.quarantine <folder>`, or use **System Settings → Privacy & Security → Open Anyway**.
  - The **LimitSwitch** app the installer creates opens without that warning, because it's made on your Mac.

Each run:
1. Makes sure Python 3.10+ and git are there. Windows installs them with winget; macOS asks for Apple's command line tools.
2. Updates this folder from GitHub (`main`). Local edits and local-only commits are never lost: without `--force` it stops and says why, and with `--force` it saves them to `git stash` or a backup branch first.
3. Sets up a private Python environment (`.venv`) with this OS's requirements; they're only reinstalled when they change.
4. Puts the app where you'd expect it: a Start menu shortcut on Windows, **LimitSwitch** in Applications on macOS (`/Applications`, or `~/Applications` if that isn't writable).
5. Closes any running copy and starts the new version. Its output is also saved to `update.log` next to `app.log`.

Options: `--branch NAME` (follow another branch), `--force`, `--no-launch`. A copy from source also offers updates in Settings; there, **Update** runs this installer.

A first install from nothing: `git clone https://github.com/doge006/LimitSwitch.git`, then run the installer in that folder.

## Development (any OS)

```sh
python -m account_switcher.tray          # the app (from a source copy: .venv\\Scripts\\python on Windows)
python -m account_switcher.tray --demo   # sample accounts, no real logins touched (Linux: needs python3-tk)
python -m unittest discover -s tests -v
```

Real-account tests use fake login files and a fake provider API. Tray tests use pystray's dummy backend and need `pystray` + `Pillow`. Outside Windows and macOS, saved logins are stored unencrypted in owner-only files; that fallback is for development only. The local API listens on 127.0.0.1 only and needs a per-run token.

## Demo mode

`--demo` shows sample accounts instead of your real logins, with no network use. It's for screenshots and the Windows smoke test.

## Measuring performance

`scripts/measure.py` measures the running app: memory (working set and private), CPU share and threads over a stretch of time.

```powershell
.venv\Scripts\python -m pip install psutil     # once (an installed copy: runtime\python.exe -m pip ...)
.venv\Scripts\python scripts\measure.py        # 60 s; --seconds N
```

Measure the tray alone, with the panel open, and with the full view open, a few times each, and quote the typical number with the machine it ran on.

## Publishing a release

Bump `VERSION` in `account_switcher/version.py`, merge, then run the **Release** workflow (Actions tab). It builds `LimitSwitch-Setup.exe` (`scripts/build_windows.ps1`, Inno Setup), installs it silently and checks that the app runs, installs it again over the running copy (as an update does), uninstalls it, then publishes release `v<VERSION>`. Run it with **Publish** off to build and test only; the installer is then kept as a download on the run for 7 days. Users get it on their next launch.

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
- `scripts/build_windows.ps1` + `LimitSwitch.iss`, `win_launcher.c`: the Windows installer (the app, its own Python and `LimitSwitch.exe`).
- `scripts/make_icons.py`: draws the app icon. `scripts/make_media.py` draws the README's screenshots and GIF (the **Media** workflow runs it on Windows).
- `docs/media/`: the README's screenshots and GIF.
