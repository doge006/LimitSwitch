The app offers it in Settings → **Update to 1.0.5** (or download below).

## Fixed

- **Claude logins no longer expire on their own:** the app renews the saved logins of the accounts you aren't using, but Claude's sign-in service sits behind Cloudflare, which refused the app's requests (error 1010, shown as a 403). The app read that as "Login expired" and gave up on logins that were still good. Renewals now identify as Claude Code's own client, and a Cloudflare block is retried later instead of counting as an expired login. If an account already shows "sign in again", press Refresh first: its login may still be good.
- **A renewed login is saved at once:** renewing spends the old single-use refresh token, and the usage check right after it (which Anthropic throttles hard) could fail and lose the new tokens. They are now saved the moment the renewal succeeds. Codex logins get the same protection.

## Download

| | |
|---|---|
| **Windows** | `LimitSwitcher-Setup.exe` |
| **macOS** (Apple silicon, M1 or later) | `LimitSwitcher-AppleSilicon.dmg`, or the one-line Terminal install in the [README](https://github.com/doge006/LimitSwitcher#install-macos) |
