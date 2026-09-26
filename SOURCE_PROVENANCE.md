# Vendored source

Both upstream repositories are included as ordinary source folders, with our local modifications. No submodule initialization is required. Their original licenses and notices are retained. Upstream Git metadata and release workflows are excluded.

- `codex-vitals-source/`: https://github.com/Joowonoil/Codex-Vitals at `88f33f936230f37bb890cd6465e36fdf6ee21455`. Kept as reference (notably its Windows Codex usage client, `windows/codexvitals_windows/codex_api.py`); nothing from it is loaded at runtime. `presentation_logic.py` uses a type-only auth import. The upstream app startup/auth/updater paths are unused.
- `proxy-fork/`: https://github.com/router-for-me/CLIProxyAPI at `9bdde54b59d1af70ae0534a0ef61b2c3361a1257`. Local change to `cmd/server/main.go` disables the Antigravity updater in local-model mode; a reference patch is in `experiments/proxy/account-switcher.patch`.

Build outputs, downloaded toolchains, caches, raw experiment transcripts and local continuity notes are not included. Experiment result summaries and reproducible scripts remain.
