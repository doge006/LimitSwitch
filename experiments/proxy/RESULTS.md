# Pinned CLIProxyAPI integration spike

Tested 2026-09-25 on Windows; upstream source commit `9bdde54b59d1af70ae0534a0ef61b2c3361a1257`, plus the narrow local startup fix described below. Build: portable official Go 1.26.8, `go build -trimpath -ldflags='-s -w' ./cmd/server`. Download SHA256 verified against go.dev: `b92c3b2adae85a11ba71fe7216daf0d84e82af4c8ab6c5625807f28622043a59`. Build/runtime downloads stay under this experiment directory.

## Observed behavior

The compiled **actual fork executable**, two dummy loopback Anthropic upstreams, and the HTTP API completed these checks:

1. `/v1/models` listed `claude-sonnet-4-6`.
2. `POST /v1/messages` initially selected A and returned A's response.
3. `PATCH /v0/management/claude-api-key` with `{"index":1,"value":{"priority":20}}` changed the next request to B.
4. Restoring B priority to 0 and making A return HTTP 429 caused exactly **A then B** within one downstream request. The successful response came from B; both upstream requests retained the same model.
5. In the reusable fixture, complete SSE preserved `message_stop`; partial text followed by an SSE error preserved the error and omitted `message_stop`; settling failure and another request completed from B.

These verify real proxy routing against synthetic accounts. They do not verify provider subscription credentials, official CLI handling, quota usage retrieval, or recovery of tool side effects. Official CLI experiments are separate.

`verification.json` records actual requests, response bodies, and 30 OS samples. The isolated proxy process after requests consumed **48.15 MiB peak idle WorkingSet**, **71.95 MiB peak idle PrivateBytes**, and **0.000 CPU seconds over 30 seconds**. The compiler, Python fixture, UI and official client are excluded from these specific proxy-only values. This binary alone exceeds a strict 30 MB total target. No working-set trimming was used. Under-load memory and long-running behavior remain unmeasured.

## Local-only blocker discovered

Even with `-local-model` and management asset downloading disabled, the unmodified executable starts an Antigravity manifest updater. The first test log confirms it fetched a version from an external service. No user credentials were available to that process; all inference traffic went to dummy loopback servers.

`runtime.py` blocks this updater by setting the subprocess HTTP/HTTPS/ALL proxy to a closed loopback port while dummy account entries explicitly use `proxy-url: direct`. The fixture log verifies the updater fails locally with `proxyconnect tcp: dial tcp 127.0.0.1:1`. This is an experimental containment measure for the known standard HTTP client, not a complete OS network sandbox. Production must remove unrelated provider startup hooks and audit remaining egress.

**Resolved in the built local fork:** both `StartAntigravityVersionUpdater` calls in `cmd/server/main.go` are now guarded by `if !localModel`. This covers standalone TUI and ordinary server startup. Existing model-catalog startup already skips all three catalogs when local-model is enabled; management asset startup checks disable-control-panel and disable-auto-update-panel before fetching. The fixture supplies both flags. `verify_runtime.py` confirms the patched startup log has neither updater launch nor manifest attempts. This is source and log verification, not a packet capture or full-process egress audit. The closed-proxy fallback remains additional containment.

## Reproduce

```powershell
cd D:\Account-Switcher\experiments\proxy
.\build.ps1
python .\verify_proxy.py
```

`verify_proxy.py` reproduces the routing and measurement baseline using the current executable. `verify_runtime.py` exercises the safer reusable fixture, including auto-swap OFF across repeated client requests and partial-stream recovery. Both use isolated dummy authentication directories and stop their own processes. The reusable fixture removes its temporary config/auth/log directory after closing.

## Reusable fixture API

```python
from experiments.proxy.runtime import ProxyFixture
fixture = ProxyFixture(on_request=lambda event: print(event)).start()
# fixture.base_url; fixture.api_key; fixture.management_key
fixture.set_active('B')
fixture.set_auto_swap(False)  # Reserve model becomes ineligible, including across client retries.
fixture.modes['A'] = 'quota'  # normal, quota, partial
# For an interrupted-turn test: fixture.modes.update(A='partial', B='partial')
# After the official client reaches a terminal failure:
fixture.settle_failure()
# Then submit Continue to that same client session.
fixture.stop()
```

`start()` returns the fixture. `events` is a deque bounded to 256 account/model/mode/path/timestamp records, `process` is the subprocess handle, and `directory` holds its config/log until close. API and management keys are randomly generated per fixture. The callback runs on upstream request threads. GUI callbacks must marshal to their UI thread. `set_active`, `set_auto_swap` and `settle_failure` perform blocking HTTP calls and brief reload settling, so run outside a GUI event handler. `settle_failure` changes B's synthetic identity to clear synthetic cooldown; this is test fixture behavior, never a real-account recovery procedure. Partial mode produces an SSE error after text when streaming and HTTP 429 for nonstream fallback. Test A partial/B quota, wait for the official client terminal failure, then settle and submit the follow-up.

`recover_to('A'|'B')` generalizes settlement: the target becomes normal with a fresh dummy identity, the other account becomes quota-limited, and selection changes to the target. `settle_failure()` calls `recover_to('B')`. `reset_accounts()` restores both normal dummy accounts with fresh identities, resets selection to A, clears recorded events, preserves the auto-swap toggle, and keeps the same server URL. These operations also passed real-fork tests, including recovery to A while auto-swap is off and retained A selection after enabling it again.

Final checks: `go test ./cmd/server` passed; `verify_runtime.py` passed all assertions. `runtime-verification.json` stores the final checklist. The actual binary includes the local updater fix. Baseline memory values above precede that small fix and remain the available proxy benchmark.

Selection tests disable session affinity: upstream documents that an established session binding outranks changed priority. A real per-session Swap must explicitly replace the binding or route by selected identity; this API call alone does not implement that case.

## Login and secure persistence

Upstream supports these commands, inspected from source but intentionally **not executed**:

```powershell
.\cli-proxy-api.exe -config .\config.yaml -claude-login
.\cli-proxy-api.exe -config .\config.yaml -codex-login
.\cli-proxy-api.exe -config .\config.yaml -codex-device-login
```

Optional `-no-browser` and `-oauth-callback-port` exist. Auth is written beneath `auth-dir`; use an isolated manager-owned directory, never the official client's existing auth directory.

`internal/auth/claude/token.go` and `internal/auth/codex/token.go` use `os.Create` plus JSON encoding and store access/refresh/ID tokens in plaintext. Unix `0700` directory permissions are insufficient Windows ACL protection, and files are not encrypted. The shared `sdk/auth/manager.go` invokes its store's Save method. `sdk/auth.RegisterTokenStore` accepts a `sdk/cliproxy/auth.Store` implementing List/Save/Delete, providing a useful encrypted-store integration seam.

Next production step: integrate DPAPI-backed persistence and explicit Windows ACLs through that store, then audit watcher and management import/export/refresh code for direct disk writes. Keep refreshed secrets owned by one process. Do not decrypt credentials into temporary JSON files as a shortcut. Only after this work and provider compatibility validation should real account login be enabled. No OAuth flow or existing credentials were touched in these experiments.

## Remaining gates

- Strict 30 MB budget requires trimming or a smaller router; this build fails that target.
- Actual provider usage API, multi-account OAuth, model entitlement, refresh rotation and policy compatibility remain unverified.
- CLI mid-response continuation must be tested against official client state, separately from proxy streaming.
- VS Code and desktop active-session continuation remain separate integration work.
- Native UI integration is performed by the main implementation, outside this experiment.
