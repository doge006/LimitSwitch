"""Start LimitSwitch in the tray / menu bar without a console window (used by the Start
menu shortcut, start at sign-in and the macOS app). A startup error is written to app.log and,
on macOS, shown in an alert, since there is no console to print it to."""
import os
import sys

root = os.path.dirname(os.path.abspath(__file__))
os.chdir(root)
sys.path.insert(0, root)
if sys.platform == "win32" and os.path.basename(sys.executable).lower() == "limitswitch.exe":
    # Started by LimitSwitch.exe, which runs Python in its own process (so Task Manager shows
    # LimitSwitch). Helpers the app starts (Claude Code's hook and status line) need Python itself.
    sys.executable = sys._base_executable = os.path.join(root, "runtime", "pythonw.exe")
# The DMG's app has its code and Python inside LimitSwitch.app, which must stay unchanged (its
# signature covers every file): no .pyc files (they are built in) and no runtime folder there.
BUNDLED = sys.platform == "darwin" and ".app/Contents/Resources/" in root + "/"
if BUNDLED:
    sys.dont_write_bytecode = True
if sys.prefix == sys.base_prefix and not BUNDLED:  # started by the macOS app's launcher: use the .venv's packages
    import glob
    import site
    for folder in glob.glob(os.path.join(root, ".venv", "lib", "python%d.%d" % sys.version_info[:2], "site-packages")):
        site.addsitedir(folder)


def report(text):
    if sys.platform == "win32":
        folder = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "AccountSwitcher")
    elif sys.platform == "darwin":
        folder = os.path.expanduser("~/Library/Application Support/AccountSwitcher")
    else:
        folder = os.path.expanduser("~/.local/share/account-switcher")
    os.makedirs(folder, exist_ok=True)
    log = os.path.join(folder, "app.log")
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("Startup failed:\n" + text + "\n")
    if sys.platform == "darwin":
        import subprocess
        last = (text.strip().splitlines() or ["unknown error"])[-1].replace('"', "'")[:300]
        subprocess.run(["/usr/bin/osascript", "-e",
                        f'display alert "LimitSwitch couldn\'t start" message "{last}\n\nDetails: {log}" as critical'],
                       check=False)


if sys.platform != "win32" and sys.stderr is not None:
    import faulthandler
    import signal
    faulthandler.register(signal.SIGUSR1, all_threads=True)  # kill -USR1: where is it stuck?

try:
    from account_switcher.tray import main
    if BUNDLED:
        from account_switcher.tray import user_url_file
        url_file = str(user_url_file())
    else:
        url_file = os.path.join(root, ".runtime", "tray.url")
    main(["--quiet", "--url-file", url_file, *sys.argv[1:]])
except SystemExit:
    raise
except BaseException:
    import traceback
    report(traceback.format_exc())
    raise
