# Account Switcher: technical plan and feasibility decisions

Date: 2026-09-25. Platform: Windows first. Status: researched design, not an implemented or live-tested system.

## 1. Outcome and limits

Build our own locally maintained account manager for Claude Code and Codex. Show aliases, identity, provider-reported usage windows, reset countdowns, active bindings, manual Swap, automatic quota failover, and per-session AFK recovery. Preserve the official CLI interface by running the original executable. Start only on explicit launch; terminate all manager-owned helpers on Quit. No startup service, telemetry, cloud account store, or automatic upstream updates.

This document is the proposed implementation plan for the current request. CODEX_ACCOUNT_DASHBOARD_PLAN.md remains an older background document, unchanged. Its instructions are not authorization to execute it. This request authorizes research and writing a plan, not live credential changes or implementation.

Corrections to earlier feasibility statements: a gateway-capable client is not automatically controllable by another program. Automatic pre-output failover is feasible, but mid-response recovery in every official desktop/extension UI is unverified. Thirty MB is a target, not a measured result. Some blockers require runtime experiments or provider clarification and cannot honestly be declared solved on paper.

## 2. Evidence collected locally

Read-only help/version and installed extension manifest checks found:

| Component | Observed |
|---|---|
| Claude CLI | 2.1.283; C:\Users\daniel\.local\bin\claude.exe |
| Codex CLI | 0.144.6; npm-installed launcher |
| Claude VS Code extension | 2.1.263, win32-x64 |
| Codex VS Code extension | 26.903.61454, win32-x64 |
| Rust toolchain | rustc and cargo commands present; builds not tested |

The Claude extension manifest exposes claudeCode.claudeProcessWrapper and environmentVariables. That is a possible integration point, not proof of its input/output protocol. Neither inspected extension command manifest advertised an arbitrary send-message-to-session command. Internal commands may exist; they are not established public APIs. Codex CLI help exposes app-server proxy and JSON schema generation. Neither its presence nor a second app-server process establishes control of an already-running desktop chat.

No secrets, live account data, conversation archives, or effective auth configuration were inspected. Desktop versions, WSL/remote execution locations, actual account entitlements, token renewal, cross-account continuation, and runtime memory remain unverified.

## 3. Architecture decision

Use two clearly separated responsibilities:

1. Request router: routes an inference request to the selected eligible account, maintains session affinity, refreshes credentials through validated adapters, and handles recoverable quota failures before output is committed.
2. Session controller: detects a failed, settled client turn and submits one continuation message when AFK is armed. It never executes model tool calls itself.

Data path:

    Official client -> loopback inference router -> provider
    Manager/UI -> local authenticated control channel -> router/session controller

The manager owns only local storage and control. Inference still reaches the provider. The router sees prompts and responses in memory; it does not log them by default. Switching applies to sessions explicitly enrolled in this route, not every client on the machine.

### Implementation choice

Start with a pinned, personally maintained CLIProxyAPI fork for compatibility experiments, plus a small Rust supervisor. It already implements provider-specific behavior that a generic reverse proxy lacks. Disable unrelated providers, built-in web management where unnecessary, update checks, request logging, identity obfuscation, model substitution, media relays, and cross-provider translation. Verify actual behavior rather than assuming flags remove all activity.

Do not simultaneously write a replacement proxy. Benchmark the maintained fork first. If it misses the memory target materially, profile and remove unused paths. Only if a strict cap is confirmed and this fails, implement the narrow required transport in Rust using the proven fixtures. A complete proxy rewrite before those measurements would trade a known compatibility base for speculative savings.

Use claude-swap as a reference for account-specific credential handling, usage normalization, outgoing token freshness, locks, and error/backoff behavior. Do not embed its Python runtime alongside the Go proxy and Rust supervisor. Preserve licenses for code actually copied; a Rust port of logic does not erase attribution obligations.

Upstream revisions resolved during research:

- CLIProxyAPI: 9bdde54b59d1af70ae0534a0ef61b2c3361a1257.
- claude-swap: 9aa6d0292736173e70d4c5d8e2026210fe39a9ee.

These are inspection references, not audited releases. Review the exact adopted files and dependency licenses before using real accounts. No source is automatically updated.

## 4. Resource budget and user interface

Provisional assumption pending the user's preference: reliable operation is primary; approximately 30 MB is the desired added resident-memory target. It includes all manager, router, and controller processes, not merely the Rust executable. Report their combined working set, private bytes, peak values, and CPU time. Report official clients, OS console hosts, and optional browser rendering separately so costs cannot be hidden.

A browser dashboard cannot carry a credible 30 MB total guarantee. The default low-memory GUI should use native Win32 controls with restrained custom drawing through Rust windows bindings: dark account list, usage bars, text countdowns, Swap buttons, session selection, Auto swap and AFK toggles. Draw on events; update visible countdowns at most once per minute. No continuous animation, embedded Chromium, or GPU compute. A TUI is a secondary lightweight option, not a second full application to build first.

The fork may exceed 30 MB even before UI. Measure before investing in polish. Use bounded queues, limited connections, one quota refresh at a time, and bounded replay buffers. For large requests, avoid repeated full JSON copies. Any necessary temporary request spool must be encrypted, bounded, and deleted after completion; if no secure bounded path exists, report that the request exceeds the supported resource profile.

Acceptance measurements: release builds, ten saved synthetic accounts, 10-minute idle, dashboard open/closed, one and four simultaneous streams, large context, reconnect storm, and all-accounts-exhausted. Target idle CPU under 0.1% of one core averaged over ten minutes and no GPU compute; measure Windows scheduling noise. Publish median/peak memory and CPU, not just a startup snapshot. Closing the UI can leave explicitly enabled sessions running; Quit stops routing and recovery. The UI must distinguish Close dashboard from Quit manager.

## 5. Account lifecycle and storage

Use official sign-in flows. Each account is enrolled deliberately and stored under a stable UUID; email is display data, not a filename or identity key. Record provider, organization/workspace, plan hints, entitlements, credential revision, and auth kind separately. API keys and subscription profiles have different usage semantics.

Store metadata and usage cache in SQLite under %LOCALAPPDATA%\AgentAccounts. Protect credential blobs using user-scoped Windows DPAPI and restrictive ACLs. Never expose tokens to the UI, logs, command line, repository, or browser storage. The prototype must adapt the fork's credential persistence so it does not silently create plaintext token files beside our encrypted vault. In-memory credentials still exist while in use; DPAPI is at-rest protection, not protection from same-user malware.

Single token owner per enrolled profile: coordinate refresh through the router, atomically persist rotated tokens, and use revisions to reject stale snapshots. An official client refreshing an independent copy can invalidate our copy; detect and require reconnect rather than repeatedly restoring it. Do not call logout merely to change accounts because token revocation can invalidate saved profiles. Test real sign-in behavior in isolated profile locations before enrollment.

A global credential-file swap is a separate manual compatibility feature and is outside the first proxy build. It has concurrent-writer and reload hazards and does not provide automatic continuation.

## 6. Usage and account selection

Normalize provider data into UsageWindow(key, scope, usedPercent, duration, resetsAt, observedAt). Support missing windows and model-specific caps; never invent a five-hour window or interpret unknown as zero. Display Used consistently, with green below 70%, amber below 90%, red at 90% and above. Show stale/error text and last successful refresh.

Use Codex's documented account/rateLimits/read where an isolated authenticated instance can query a saved account without changing the live client. Do not keep an app-server per account running just for quota. Prototype its overhead; otherwise use a reviewed provider adapter from the fork. Claude's upstream usage adapter relies on subscription OAuth behavior rather than a guaranteed public account-management API; isolate it as experimental.

Manual refresh and refresh-on-open use caching and backoff. While active, refresh relevant accounts on a conservative adaptive cadence, initially five minutes with jitter; inference quota signals may update cooldowns immediately. Obey Retry-After. A failed usage fetch is not proof that inference quota is exhausted.

Default routing: retain the current account while eligible. On a recognized account/model quota failure, pick an enabled account eligible for the same model and workspace constraints. Do not silently downgrade models, use paid overages, or switch providers. Exclude expired/revoked/held credentials. Manual Swap updates the selected session's next-request binding; it does not abort a healthy stream. Show Pending until the next request confirms the new binding. A provider-wide default applies to newly enrolled sessions only.

Separate model quota, account quota, service overload, network failure, authentication failure, billing hold, and user cancellation. Service-wide failures back off rather than cycling every account. Ban/hold/policy failures stop and request attention. Exhausted pools enter Waiting; use a scheduled wake at the earliest credible reset, then revalidate. A timestamp is not proof of renewed capacity. Without a reliable reset, back off with a visible next check.

## 7. Automatic retry before output

Track each logical request and attempt. Retain bounded replay material until the safe retry boundary closes. Authenticate every loopback inference request using a session-scoped local capability; replace that local capability with the selected provider credential only for the allowlisted provider origin.

A quota rejection may be HTTP 429 or an error event within HTTP 200. Inspect the stream bootstrap. Commit the stream once a generated or side-effect-bearing event is exposed; use bounded bootstrap buffering and a real independent deadline. Do not buffer an entire answer to manufacture retryability.

Retry a known rejection on an eligible account only when no response has been committed and no upstream tool operation may have executed. Absence of visible text is insufficient: provider-hosted search or other work may already have started. Network timeouts with uncertain acceptance require reconciliation or stop. Limit retries to distinct eligible accounts and an overall deadline. Client cancellation cancels every attempt immediately.

Once committed, forward the proper terminal failure to the client. Never splice two generations, fake a completed tool call, or turn partial JSON into executable output.

## 8. AFK: yes, the recovery prompt can be Continue

The user's proposed message is sufficient for many settled sessions. The difficult part is delivering it once, to the right chat, at the right time. Default text:

    Continue the interrupted task from the last confirmed state. Check the outcome of any interrupted tool action before repeating it. Keep the existing scope and approval requirements.

Offer literal Continue as a preset. Prompt wording is guidance, not an exactly-once execution guarantee.

States: Running -> FailureObserved -> Settling -> ReadyToResume -> Continuing -> Running. Alternatives: WaitingForQuota, NeedsApproval, AmbiguousOutcome, UnsupportedController, Finished, Stopped.

Rules:

- AFK is explicit and session-scoped; default off on new sessions and after manager restart.
- A recognized recoverable failure must be correlated to this session and request. Normal completion never triggers Continue.
- Wait for the client to finish its built-in retries and report an ended turn/idle prompt. Wait for tool activity to settle; pending approval or user question pauses recovery.
- Confirm the transcript is coherent. Partial thinking/tool blocks and uncertain effects require reconciliation; if it cannot be proved safe, pause.
- Update routing, then submit exactly one continuation for this failure generation. Track session ID, failed turn, recovery generation, submission intent, and acknowledgement.
- On crash between send and acknowledgement, reconcile the transcript/events before any resend. If acknowledgement remains uncertain, stop. A local journal alone cannot guarantee exactly-once delivery.
- Any user input, cancellation, closed session, or disabled AFK invalidates pending recovery. A UI draft must never be overwritten.
- Default limits: three continuation attempts per incident, five consecutive failures per session, 24-hour AFK expiry, and visible stop controls. Waiting for a known reset is separately bounded by expiry.
- Existing permissions stay intact; AFK never clicks approval, expands access, or bypasses client safeguards.

## 9. Client controllers and rollout matrix

### Claude CLI: first full AFK target

Launch the unmodified CLI through our Windows ConPTY supervisor. Forward terminal output/input, resize, colors, mouse reporting, Unicode, paste, Ctrl-C and exit status so the official interface remains visible. Launch-time scoped settings add failure/tool lifecycle hooks that notify our local control channel; preserve existing settings and hooks. No global instruction-file edits.

StopFailure reports session/error but cannot continue the turn [S2]. Correlate it with proxy failure and the controlled terminal's idle state. Track user input and require an empty input buffer. Only then write the continuation text and submit through the owned pseudoconsole input. No global keyboard injection. Prompt recognition is version-specific and must fail closed. If a reliable idle signal cannot be established, AFK stays unavailable for that version.

Alternative recovery after an owned process has actually exited: start the same official CLI with the exact saved session ID and continuation prompt. Never use most-recent-session selection; never launch a second writer while the original session is live. This fallback may visibly restart the terminal UI. JSON streaming mode is easier to control but changes the interactive experience, so offer it only as a separate supervised mode, not as a claim of identical UI.

Existing terminals launched outside the supervisor need a one-time resume through our launcher for full AFK. Proxy-only routing can work without terminal ownership, but post-error input cannot be guaranteed.

### Codex CLI

Use ConPTY for appearance, but prefer structured state from the same runtime when available. Investigate the installed app-server control socket/proxy transport without assuming every TUI uses it. If an authorized connection owns/observes the exact thread, wait for failed turn completion and start a continuation via turn/start. Handle approval requests through the original client. thread/resume on an unrelated second server is not a safe attachment mechanism.

If shared control is unavailable, certify a version-specific terminal controller or expose supervised app-server mode with its UI difference clearly stated. Preserve thread/model/working directory and permissions.

### Claude VS Code extension

Routing is documented through environmentVariables. Inspect and prototype the advertised claudeProcessWrapper in a disposable VS Code profile: determine arguments, stdio framing, result/error events and user-input acknowledgements. A transparent wrapper could insert a continuation after a failed result while forwarding everything to the official extension, but this is an experimental protocol integration. It is acceptable only if the UI shows the new message and running state correctly, handles cancel/approvals, and has no concurrent writer race.

If the wrapper fails these tests, retain pre-output failover and show Resume needed after midstream failure. Supported AFK fallback: supervised official CLI in VS Code's integrated terminal. Do not describe that fallback as AFK inside the extension panel.

### Codex VS Code extension

Routing uses shared Codex config. First investigate authenticated attachment to the exact extension app-server with reliable lifecycle notifications. Its cliExecutable override is explicitly development-only: do not silently replace it with a wrapper as the default solution. A custom shim requires separate compatibility testing and is a last-resort experimental option. Without verified same-thread control, support pre-output failover only; offer supervised CLI fallback.

### Desktop apps

Claude Desktop has a distinct third-party inference configuration. Codex local agents share configuration concepts with CLI/IDE. Both require installed-version routing tests and a separate continuation-controller test. Documented routing is not a public send-message API. No unattended UI clicking, focus stealing, binary patching, or use of this Codex chat's private tools as a shipped integration.

Until a stable authenticated session-control interface is demonstrated, desktop support means local inference routing and pre-output failover, plus manual continuation after committed-stream failure. Ordinary consumer chats, Cowork, remote/cloud tasks and desktop sign-in identity are not automatically switched. Claude gateway mode also changes available remote features [S3]. Full desktop AFK remains a blocker, with supervised CLI as the concrete fallback.

## 10. Account-bound context

Retain account affinity for a conversation and its subagents where supported. Swapping tokens does not transfer provider-owned files, caches, response IDs, encrypted reasoning, or remote tasks.

For Codex WebSockets/previous_response_id, reconnecting to a new account may require a fresh request with complete context and no previous response reference [S6]. Validate whether the official client sends that context. If only a delta is available, request a client-managed continuation/rebuild; never silently drop context or invent missing reasoning. Keep WebSocket steering disabled in the first profile unless required and tested; do not assume transport can always be forced to HTTP.

For Claude, verify cross-account reuse of conversation blocks, tool results, images, thinking signatures and caches. If the provider rejects account-bound artifacts, mark that session non-migratable and pause. A text handoff into a fresh session is a separately labelled lossy fallback requiring user choice, not seamless recovery.

## 11. Control surface, models and layout

Core models: Account; CredentialRecord(revision, secretRef, expiry); UsageSnapshot(windows, observedAt, stale/error); SessionBinding(client, runtimeVersion, environment, threadId, accountId, pendingAccountId); RecoveryIncident(requestId, turnId, phase, attempts, acknowledgement); Capabilities(route, preOutputFailover, idleSignal, submit, reconcile); Settings.

Settings: autoSwap, afk per session, account priority, model eligibility, retry bounds, AFK expiry, waitForReset, refresh cadence, resource profile. AFK can be armed only when the controller exposes the required capabilities. AutoSwap and AFK are separate: AutoSwap can route requests; AFK permits a follow-up prompt after a settled failure.

Native UI: provider-grouped account rows, Used bars and reset times, active and pending labels, per-session selector, Swap, Refresh, Enable account, Reconnect, AutoSwap, AFK and Stop. Show unsupported capabilities and stale data explicitly. Local event history includes switches, retries, reasons and timestamps but no prompts or tokens.

Suggested tree:

    Cargo.toml
    src/{main,service,models,store,vault,ipc,selection,recovery}.rs
    src/controllers/{conpty,claude,codex}.rs
    src/ui/{window,accounts,sessions,settings}.rs
    proxy-fork/              # maintained separately at an exact revision
    tests/{fixtures,recovery,security,compatibility,performance}/
    docs/{COMPATIBILITY,UPSTREAM,PERFORMANCE,SECURITY}.md

Single local service instance per user, authenticated IPC with same-user ACLs, bounded event messages, atomic settings updates. Desktop/IDE routing settings use reversible field-level changes with backup and conflict detection; never restore an entire stale config over later user edits. Native Windows, WSL, SSH and remote extension hosts are separate environments; localhost is not interchangeable. Start with native Windows only.

## 12. Blocker register and resolution criteria

| Blocker | Resolution / fallback | Status |
|---|---|---|
| Midstream output cannot be transparently replaced | End failed stream honestly; controller submits one new turn after settling | Designed; client tests required |
| Sending Continue to wrong/busy UI | Owned ConPTY or verified same-runtime API; user-input cancellation and acknowledgement | CLI prototype required |
| Partial tool action may already have executed | Reconcile tool result/state; pause ambiguous cases | Fundamental limit; no universal exactly-once guarantee |
| GUI extension/desktop lacks public submission API | Bounded wrapper/socket experiment; otherwise supervised CLI fallback | Unresolved for full GUI AFK |
| Account-bound server state | Affinity and complete-context recovery; pause unsupported artifacts | Cross-account live tests required |
| Refresh-token copies race | Single owner, versioned vault writes, reconnect on invalidation | Design resolved; integration required |
| Fork writes plaintext credentials | Replace persistence with DPAPI-backed adapter before live use | Implementation prerequisite |
| 30 MB may be unrealistic | Measure combined processes; trim fork before considering narrow Rust port | Unmeasured; preference pending |
| Closed manager cannot auto-recover | Explicit working-session lifetime and separate Quit | Design resolved |
| All accounts exhausted | Scheduled bounded revalidation, then stop/notify at expiry | Design resolved |
| Client updates break controller | Versioned capability matrix, fixture tests, disable unverified AFK | Design resolved |
| Provider permission for subscription pooling | Seek explicit provider clarification; legal gateway support is not permission to pool tokens | External unresolved constraint |

Anthropic restricts third-party handling of subscription credentials and OpenAI prohibits circumvention of rate limits [S9,S10]. Owning multiple paid accounts, running locally, or forking software does not itself establish permission. Keep technical feasibility separate from permitted deployment. API-key gateway mode is a different billing model and must not be substituted silently for the requested subscriptions. No sign-up automation, enforcement-evasion logic, identity disguise or ban failover.

## 13. Implementation order and exit criteria

1. Compatibility and recovery spike before dashboard polish. Build synthetic provider simulator with rejection, partial text, partial tool JSON, delayed acceptance, cancellation and account-bound state. Exercise official clients against it using isolated configurations and dummy credentials. No real provider calls needed for transport tests. Record exact installed versions and routes.
2. Prove ConPTY pass-through and Claude settled-failure continuation. A counter-file tool fixture must execute once despite a midstream failure; Continue must appear once, and user input/approval must stop recovery. Repeat for Codex. If original-UI control cannot pass, document the supported supervised mode and stop claiming parity.
3. Benchmark pinned fork plus minimal supervisor/UI. Measure all resource scenarios in section 4. Choose trimmed fork or Rust port based on evidence and the user's memory preference.
4. Build encrypted vault, account metadata and honest usage UI with synthetic fixtures. Verify no credentials appear in DTOs, logs, temp files or error reports. Integrate official enrollment and provider adapters only after persistence and renewal tests pass.
5. Implement affinity, manual Swap and quota failover. Verify correct provider/account/model, no cross-session effect, rejection-before-output retry, cancellation propagation, and exhausted-pool waiting.
6. Implement AFK state machine, incident journal and crash reconciliation. Fault-inject before/after submission and acknowledgement. Require bounded retries, no continuations after success, and no duplicate recovery after crash.
7. Run extension-wrapper and desktop-control experiments in disposable profiles. Promote each capability independently. Test message visibility, cancel, permissions, tool status, context preservation and concurrent chats. Record unsupported combinations without blocking the proven CLI release.
8. With user-owned accounts and approved client configuration changes, run bounded live compatibility checks. Confirm account attribution, quota refresh, token renewal, same-model continuation and reset recovery. Avoid deliberately exhausting subscriptions; use simulator failures for most regression coverage.
9. Package local builds, document start/quit/revert/reconnect and pin updates. Verify offline account display, loopback authentication, no unexpected egress, no scheduled/startup registration, and no orphan manager-owned helpers after quit.

Release acceptance: requested dashboard and manual switch verified; pre-output failover verified per client; AFK only enabled on certified controllers; resource report published; exact limitations visible. A passing build alone cannot satisfy these criteria.

## 14. Research verification and sources

Completed: official documentation inspection; selected pinned upstream source/config/license inspection; local help/version and extension manifest inspection; independent review of midstream and account-state risks. Independent reviewer hit its usage limit before a final report; its delivered findings were checked against sources. No application code, runtime benchmark, real account switch or live AFK test was performed. Planning decisions are not test results.

[S1] Claude CLI flags and resume: https://code.claude.com/docs/en/cli-reference
[S2] Claude hooks, StopFailure and tool lifecycle: https://code.claude.com/docs/en/hooks
[S3] Claude gateway configuration for CLI, VS Code and Desktop: https://code.claude.com/docs/en/llm-gateway-connect
[S4] Codex app-server protocol and turn control: https://learn.chatgpt.com/docs/app-server
[S5] Codex custom providers/shared configuration: https://learn.chatgpt.com/docs/config-file/config-advanced and https://learn.chatgpt.com/docs/developer-settings
[S6] Responses WebSocket reconnect/state recovery: https://developers.openai.com/api/docs/guides/websocket-mode
[S7] Claude streaming/error recovery: https://platform.claude.com/docs/en/build-with-claude/streaming
[S8] Windows pseudoconsole: https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session
[S9] Anthropic authentication policy: https://code.claude.com/docs/en/legal-and-compliance
[S10] OpenAI terms: https://openai.com/policies/row-terms-of-use/
[S11] Pinned CLIProxyAPI configuration: https://github.com/router-for-me/CLIProxyAPI/blob/9bdde54b59d1af70ae0534a0ef61b2c3361a1257/config.example.yaml
[S12] Pinned claude-swap: https://github.com/realiti4/claude-swap/tree/9aa6d0292736173e70d4c5d8e2026210fe39a9ee
[S13] CLIProxyAPI license: https://github.com/router-for-me/CLIProxyAPI/blob/9bdde54b59d1af70ae0534a0ef61b2c3361a1257/LICENSE
