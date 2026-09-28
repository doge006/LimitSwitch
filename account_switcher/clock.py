"""The system's clock format, on its own so the resident app can ask without loading the drawing code."""
from functools import lru_cache
import sys
import time


@lru_cache(maxsize=1)
def clock_12h():
    """Does this user's clock show AM/PM? (Windows: the short time format; elsewhere the locale.)"""
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:
                return "h" in winreg.QueryValueEx(key, "sShortTime")[0]
        except OSError:
            return False
    try:
        import locale
        locale.setlocale(locale.LC_TIME, "")
        return bool(time.strftime("%p", time.localtime(0)))
    except Exception:
        return False
