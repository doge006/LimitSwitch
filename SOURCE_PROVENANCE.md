# Upstream sources

No upstream source is vendored in this repository.

- `account_switcher/providers.py` follows the usage clients of Codex Vitals (https://github.com/Joowonoil/Codex-Vitals at `88f33f936230f37bb890cd6465e36fdf6ee21455`, MIT; see `THIRD-PARTY-NOTICES.txt`).
- The `--demo` Recovery lab's compiled proxy is CLIProxyAPI (https://github.com/router-for-me/CLIProxyAPI at `9bdde54b59d1af70ae0534a0ef61b2c3361a1257`) with `experiments/proxy/account-switcher.patch` applied. `experiments/proxy/build.ps1` fetches and patches it.
