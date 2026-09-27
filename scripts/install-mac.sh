#!/bin/bash
# Installs (or updates) LimitSwitch on macOS from the latest GitHub release:
#   curl -fsSL https://raw.githubusercontent.com/doge006/LimitSwitch/main/scripts/install-mac.sh | bash
# It picks the DMG for this Mac (Apple silicon or Intel), closes a running copy (which puts the
# Codex and Claude Code settings back), puts LimitSwitch.app in Applications and opens it.
# Saved accounts and settings are in ~/Library/Application Support/AccountSwitcher and are kept.
#
# The app isn't notarized by Apple. macOS checks apps downloaded by a browser, and warns about
# those that aren't; a download made here (curl) isn't marked as coming from the internet, so
# there's no warning. With --dmg <file or URL>, it installs that DMG instead (testing a build);
# a file a browser downloaded has that mark, which is removed from the installed copy.
# The app's own updater runs this too (with --from-app).
set -euo pipefail

REPO="doge006/LimitSwitch"
DMG=""
FROM_APP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dmg) DMG="$2"; shift 2 ;;
    --from-app) FROM_APP=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\033[36m%s\033[0m\n' "$1"; }
fail() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = Darwin ] || fail "This installs the macOS app. On Windows, use LimitSwitch-Setup.exe from the releases page."
# Apple silicon even from a Terminal running under Rosetta.
if [ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = 1 ]; then KIND=AppleSilicon; else KIND=Intel; fi

# /Applications when this user can write there (admin accounts can), else ~/Applications.
if [ -w /Applications ]; then TARGET=/Applications; else TARGET="$HOME/Applications"; mkdir -p "$TARGET"; fi
APP="$TARGET/LimitSwitch.app"

WORK="$(mktemp -d)"
MOUNT="$WORK/mount"
cleanup() {
  hdiutil detach -quiet "$MOUNT" 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# 1. The disk image.
if [ -z "$DMG" ]; then
  DMG="https://github.com/$REPO/releases/latest/download/LimitSwitch-$KIND.dmg"
fi
case "$DMG" in
  http://*|https://*)
    say "Downloading LimitSwitch for $([ $KIND = Intel ] && echo 'an Intel Mac' || echo 'Apple silicon')..."
    curl -fL --progress-bar -o "$WORK/LimitSwitch.dmg" "$DMG" || fail "The download failed ($DMG)."
    DMG="$WORK/LimitSwitch.dmg" ;;
  *) [ -f "$DMG" ] || fail "No such file: $DMG" ;;
esac
mkdir -p "$MOUNT"
hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MOUNT" "$DMG" || fail "Couldn't open the disk image."
[ -d "$MOUNT/LimitSwitch.app" ] || fail "The disk image has no LimitSwitch.app."

# 2. Close the running copy (any kind: this one, or one installed from source), so it puts the
#    Codex and Claude Code settings back. Its launcher.conf says which Python and script it runs.
quit_copy() {
  local app="$1" conf="$1/Contents/Resources/launcher.conf"
  [ -f "$conf" ] || return 0
  local python script
  python="$(sed -n 2p "$conf")"; script="$(sed -n 3p "$conf")"
  case "$python" in /*) ;; *) python="$app/Contents/Resources/$python" ;; esac
  case "$script" in /*) ;; *) script="$app/Contents/Resources/$script" ;; esac
  [ -x "$python" ] && [ -f "$script" ] && "$python" -B "$script" --quit >/dev/null 2>&1 || true
}
for existing in /Applications/LimitSwitch.app "$HOME/Applications/LimitSwitch.app"; do
  if [ -d "$existing" ]; then
    [ $FROM_APP = 1 ] || say "Closing the running LimitSwitch..."
    quit_copy "$existing"
  fi
done
sleep 1

# 3. Replace the app (one copy only; also the one from before the rename).
say "Installing to $TARGET..."
for old in /Applications/LimitSwitch.app "$HOME/Applications/LimitSwitch.app" \
           "/Applications/Account Switcher.app" "$HOME/Applications/Account Switcher.app"; do
  if [ -d "$old" ]; then rm -rf "$old" 2>/dev/null || fail "Couldn't remove the old $old (try closing it first)."; fi
done
ditto "$MOUNT/LimitSwitch.app" "$APP"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true
codesign --verify --deep --strict "$APP" 2>/dev/null || fail "The installed app failed its signature check; download it again."

# 4. Start it: with its window, or quietly in the menu bar after an update from the app.
if [ $FROM_APP = 1 ]; then open "$APP" --args --at-login; else open "$APP"; fi
printf '\033[32m%s\033[0m\n' "LimitSwitch is installed in $TARGET and running in the menu bar."
