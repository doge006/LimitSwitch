"""Start Account Switcher in the tray without a console window (used by Start with Windows)."""
import os
import sys

root = os.path.dirname(os.path.abspath(__file__))
os.chdir(root)
sys.path.insert(0, root)

from account_switcher.tray import main  # noqa: E402

main(["--quiet", "--url-file", os.path.join(root, ".runtime", "tray.url"), *sys.argv[1:]])
