The app offers it in Settings → **Update to 1.0.9** (or download below).

## Fixed

- **The Claude Code Status mod row in Settings is readable:** its state ("Installed · run /reload-plugins in an open session") was cut off with "…". It now wraps onto a second line, and the row is named **Claude Code Status mod**.
- **The status line no longer blinks out:** when the app was busy for a moment, the status line script showed nothing until its next run. It now shows the last line for up to 3 minutes meanwhile (and nothing after the app refuses it, for example with an old token).

## New

- **A coloured status line:** the icon is yellow and "LimitSwitcher" grey, **5h** and **1w** are blue, and what's left is green with plenty left, yellow in the middle and red when low (the context's "% left" too). This is the line the app shows in Claude Code, and the mod's line above the prompt.
- **The Claude Code Status mod draws the line itself,** above the prompt, in those colours (it used a plain pinned line before).

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
