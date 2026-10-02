The app offers it in Settings → **Update to 1.0.8** (or download below).

## Fixed

- **Auto resume no longer waits behind Claude Code's usage-limit dialog:** with Claude Code's own "continue automatically" setting off, a limit opened a "What do you want to do?" dialog, and the app's continue only ran after you answered it. Auto resume now turns that setting on while it's on (your own value is put back when it's off or the app quits), so Claude Code shows a one-line wait instead. Claude Code cancels that wait when the account is switched, and the hook skips its own continue if the session already went on by itself.
- **The usage-limit panel no longer stops redrawing:** a bar animating to zero size could make the panel or taskbar view fail with "y1 must be greater than or equal to y0". Every drawing path now skips shapes with no size.

## New

- **Large sessions ask first:** loading a big session on an account that hasn't cached it can cost a lot of usage. When a session of 400k tokens or more hits a limit, Auto swap still moves to the next account, but the session isn't continued by itself. The panel (macOS) or tray menu (Windows) offers **Continue** or **Don't**. Turn this off with Settings → **Skip large sessions**.
- **Wait for a near reset:** if Claude's 5-hour limit resets within 15 minutes, the app waits instead of switching accounts. Turn this off with Settings → **Wait for a near reset**.
- **Context in the status line:** the app's Claude Code status line shows the session's context (`ctx 183k · 82% left`).

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
