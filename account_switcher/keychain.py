"""macOS Keychain (login keychain) through Apple's `security` tool.

Values go in hex-encoded on stdin (`security -i`), never on the command line, so they do not
show up in the process list. Claude Code stores its login the same way (-X hex).
"""
import subprocess

SECURITY = "/usr/bin/security"
TIMEOUT = 15


NOT_FOUND = 44  # errSecItemNotFound


def get(service, account, strict=False):
    """The stored value as text, or None when there is no such item. With strict, any other
    failure (a locked keychain, access denied, no answer) raises OSError instead of looking
    like a missing item."""
    try:
        done = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
                              capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as error:
        if strict:
            raise OSError(f"Keychain didn't answer: {error}") from error
        return None
    if done.returncode != 0:
        if strict and done.returncode != NOT_FOUND:
            raise OSError(f"Keychain refused the read ({done.returncode}): {(done.stderr or '').strip()[:200]}")
        return None
    value = done.stdout.rstrip("\n")
    return _maybe_hex(value)


def put(service, account, value):
    """Create or update the item (the login keychain, readable by this user's apps via
    `security`). Raises OSError when the keychain refuses."""
    command = f'add-generic-password -U -s "{_quote(service)}" -a "{_quote(account)}" -X {value.encode("utf-8").hex()}\n'
    try:
        done = subprocess.run([SECURITY, "-i"], input=command, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.SubprocessError as error:
        raise OSError(f"Keychain didn't answer: {error}") from error
    if done.returncode != 0 or "error" in (done.stderr or "").lower():
        raise OSError(f"Keychain refused the write: {(done.stderr or '').strip()[:200]}")


def delete(service, account):
    try:
        subprocess.run([SECURITY, "delete-generic-password", "-s", service, "-a", account],
                       capture_output=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass


def _quote(text):
    if '"' in text or "\n" in text:
        raise ValueError("unsupported character in keychain name")
    return text


def _maybe_hex(value):
    """`security -w` prints non-text values as hex; stored text comes back as is."""
    if value and len(value) % 2 == 0 and all(c in "0123456789abcdef" for c in value.lower()):
        try:
            decoded = bytes.fromhex(value).decode("utf-8")
            if decoded.isprintable():
                return decoded
        except (ValueError, UnicodeDecodeError):
            pass
    return value
