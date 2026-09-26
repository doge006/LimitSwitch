# Official Claude CLI compatibility spike

Tested on Windows on 2026-09-25 with installed **Claude Code 2.1.283** and Python 3.13.

Run from the project root:

```powershell
python experiments/claude/spike.py
```

The script runs the actual installed `claude` executable against a Python standard-library HTTP fixture bound to 127.0.0.1 on an ephemeral port. Every run uses a fresh temporary working/configuration directory, `--bare`, empty tools, no MCP configuration, dummy API credentials, no session persistence, and disabled nonessential traffic. Existing provider environment settings are removed from the child. No real account credentials, token stores, or client configuration files are read by the test code. No real inference is performed. These settings isolate inference/auth; this is not a firewall-level proof that the executable makes zero external requests.

## Observed passing cases

| Case | Actual requests | Observed result |
|---|---:|---|
| Normal response | 1 | Official client returns `RECOVERED_OK`, `is_error:false`, exit 0. |
| Initial HTTP 429 | 2 | Official client retries and succeeds in its original process/session. |
| Partial SSE then persistent quota failure | 4 | Client first fails its stream, makes two nonstream requests that receive 429, reports terminal error, then accepts `Continue` on the existing stdin. Next response succeeds with the same session ID. |

Machine-readable request bodies, client events, terminal results and timing are in `normal-results.json`, `quota-results.json`, and `partial-results.json`. They contain synthetic prompts and dummy endpoint details, not real credentials. Assertions verify final success, retry occurrence, the same session ID after continuation, and preservation of the original user prompt.

## Exact continuation protocol

Launch with `--bare -p --input-format stream-json --output-format stream-json --verbose --include-partial-messages`. Keep stdin open. Send each input as a newline-terminated JSON object:

```json
{"type":"user","message":{"role":"user","content":"Continue"}}
```

Only send recovery after a terminal `type:result` event with `is_error:true`. **Do not treat `subtype:success` as success:** this version returned that subtype even for the error result. The preceding assistant event carried `error:rate_limit`. The result text was `API Error: Request rejected (429) · Synthetic quota exhausted`.

The recovery request retained the original `Say hello.` user prompt and appended `Continue`. The incomplete text fragment `PARTIAL_BEFORE_FAILURE` was absent from the subsequent model history. Thus this proves continuation of the task/session, not guaranteed preservation of partially displayed generation. All tools were disabled, so safe recovery of partly executed tools remains untested.

## Retry timing and discoveries

The fixture sets the documented `CLAUDE_CODE_MAX_RETRIES=1` so tests have bounded retry latency. Without this setting, a persistent quota failure did not produce a terminal result before the harness's 100-second timeout; the harness killed and reaped its own process. Thus a controller waiting for terminal failure must account for native retries. The application should coordinate retry ownership and expose its retry policy rather than assuming errors surface immediately.

An initial fixture revision returned SSE to the client's fallback nonstream request, causing a malformed-response error. This was corrected: the final fixture returns valid HTTP 429 JSON for fallback nonstream requests. Only the corrected behavior is represented as passing above.

## Scope and blockers

- This is official **headless stream-json mode**, not the ordinary interactive Claude terminal UI. It validates a controlled AFK session interface; it does not prove keystroke injection or unchanged interactive UI recovery.
- The fixture simulates account exhaustion and replacement availability, not OAuth account switching. The actual proxy needs a separate A/B upstream credential-routing test. The same-client prompt/session continuity is proven here; subscription authorization, token refresh and account-bound context are not.
- VS Code, Claude desktop, Codex, real quota formats, reasoning signatures, tool execution, attachments and context compaction are untested by this spike.
- Tools/permissions are never enabled or bypassed. Production recovery must preserve approval requirements and block ambiguous side effects.
- The controller should match structured assistant errors plus terminal `is_error`, use a per-turn recovery budget, and avoid racing a user message or an in-flight request.
- Process and HTTP server cleanup is in `finally`; temporary isolated configuration is removed at exit.

## Primary reference

- Installed `claude --help` and `claude --version` establish the tested executable and flags.
- [Official environment variable reference](https://code.claude.com/docs/en/env-vars) documents `CLAUDE_CODE_MAX_RETRIES`, `--bare`/`CLAUDE_CODE_SIMPLE`, and interrupted-session options. Accessed 2026-09-25.

No runtime application memory claim can be drawn from this test: the official client memory is distinct from manager/proxy overhead.


Repository cleanup: raw `*-results.json` transcripts were removed from the source tree. Rerun the adjacent spike script to regenerate them locally; these outputs are ignored by Git. The observations above are historical evidence, not a fresh run.
