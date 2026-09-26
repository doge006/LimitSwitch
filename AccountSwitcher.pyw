"""Start Account Switcher in the tray / menu bar without a console window (used by the Start
menu shortcut, start at sign-in and the macOS app). A startup error is written to app.log and,
on macOS, shown in an alert, since there is no console to print it to."""
import os
import sys

root = os.path.dirname(os.path.abspath(__file__))
os.chdir(root)
sys.path.insert(0, root)


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
                        f'display alert "Account Switcher couldn\'t start" message "{last}\n\nDetails: {log}" as critical'],
                       check=False)


try:
    from account_switcher.tray import main
    main(["--quiet", "--url-file", os.path.join(root, ".runtime", "tray.url"), *sys.argv[1:]])
except SystemExit:
    raise
except BaseException:
    import traceback
    report(traceback.format_exc())
    raise
