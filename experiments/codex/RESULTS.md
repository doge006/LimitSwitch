# Codex CLI compatibility spike — stopped at user interruption

Installed client: `codex-cli 0.144.6`. Windows; Python 3.13. Observed on 2026-09-25.

## Observations

- **Normal response passes:** actual `codex exec --json` sent one request to the loopback Responses API fixture, emitted `item.completed` with `RECOVERED_OK`, then `turn.completed`, exit 0.
- **Initial HTTP 429 stops the turn:** actual client emitted `error` and `turn.failed`, both with message `exceeded retry limit, last status: 429 Too Many Requests`, exit 1. Only one HTTP request was observed despite configured request/stream retry allowances. Therefore this spike does not establish native quota retry or automatic switching. A proxy should intercept the error before returning it to Codex, or a controller should explicitly resume the saved thread.
- **Partial stream/resume untested:** fixture code is present but excluded from default execution. User interruption ended experimentation before this case ran.

Raw events/request input and stderr are saved in `normal-results.json` and `quota-results.json`. Run `python experiments/codex/spike.py` to reproduce the two observed cases. The last full command before interruption exited 1 because the old assertion expected native quota retry; the saved script now explicitly asserts the observed terminal failure. This assertion-only correction has not been rerun.

## Isolation and caveats

The harness uses a fresh temporary CODEX_HOME, an empty temporary working directory, a custom loopback provider with `requires_openai_auth=false`, no API key, read-only sandbox, and no approval bypass. The fixture emits only assistant text and never tool calls. Installed client help (`exec`, `exec resume`, `app-server`) was inspected. No real account credentials were loaded by the harness, no live inference was performed, and real client configuration remained unchanged.

An initial run attempted an unauthenticated remote featured-plugin request and received HTTP 401. Setting `features.plugins=false` and `features.apps=false` removed that warning on the next run. This is not firewall-level proof of no external traffic. CODEX_HOME plus HOME/USERPROFILE overrides still did not prevent discovery of existing `~/.agents/skills` descriptions through Windows home-directory resolution; those descriptions occur in locally captured fixture input. No project contents or credential data were submitted to an external model. A stricter production test should disable skill discovery through a supported configuration or use an isolated OS user/container.

`subprocess.run` reaps each child; timeout handling kills/reaps on timeout. The server shuts down in `finally` and temporary working/config directories are cleaned up. No experiment process remains deliberately running.

The test does not establish interactive TUI, app-server turn control, VS Code/desktop, OAuth switching, account-bound context, or memory compliance.

Official reference: [Advanced configuration / custom model providers](https://learn.chatgpt.com/docs/config-file/config-advanced), accessed 2026-09-25. Installed local help is the source for the tested command flags.


Repository cleanup: raw `*-results.json` transcripts were removed from the source tree. Rerun the adjacent spike script to regenerate them locally; these outputs are ignored by Git. The observations above are historical evidence, not a fresh run.
