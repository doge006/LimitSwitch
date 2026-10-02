The app offers it in Settings → **Update to 1.0.10** (or download below).

## Fixed

- **Reinstall now really updates the Claude Code Status mod:** installing it again did nothing when it was already installed, so a newer version of the mod never arrived (you kept drawing the old plain yellow `⚠ limit-status: …` line). **Reinstall** now updates it to the newest version (restart Claude Code, or `/reload-plugins`, to apply).
- **No more empty line under the prompt with the Claude Code Status mod:** the app's status line command stayed in Claude Code's settings even though the mod draws the line, so Claude Code showed an empty row for it (and wrapped your own status line for nothing). Once the mod has reported, the app now takes its command out and puts your own status line back; it returns by itself if you uninstall the mod.
- **The status line colours:** the icon is green and "LimitSwitcher" and the account grey, **5h** and **1w** blue, each percentage green with plenty left, yellow in the middle and red when low, with "left" grey. The context is the same: `ctx 348k · 65% left`, the count and the percentage coloured by what's left. This is the line the app shows in Claude Code, and the mod's line above the prompt.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
