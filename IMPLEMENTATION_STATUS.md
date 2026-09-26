# Implementation status — 2026-09-25

The user's Web UI preference supersedes the native UI choice in ACCOUNT_SWITCHER_TECHNICAL_PLAN.md. Launch.cmd now starts the local Web UI in a browser. The old app.py entry point forwards to the same Web UI.

## Delivered and verified

- Responsive local HTML/CSS/JS dashboard, account filters, manual selection, separate Auto swap and AFK switches, activity log, session stop and server shutdown.
- Default dashboard uses the actual compiled proxy fork with two synthetic Claude upstreams, plus the installed official Claude CLI in structured, tool-free mode.
- Browser testing observed manual selection of B and successful mid-response recovery A → B in one Claude session. The toggle interaction was corrected after browser verification found an immediate state flicker.
- Request authentication and Host/Origin checks; no external frontend dependencies.
- Shut down exited the Web backend and removed its owned proxy; Stop terminated the owned Claude process. A separate subprocess test verifies automatic exit when the dashboard remains unopened past its grace period.
- Six core tests, four official-Claude simulator tests, two actual-Claude/proxy pipeline tests, and four Web/API/lifecycle tests pass. JavaScript syntax check passes.
- Web backend + proxy idle sample: 72.77 MiB working set, 89.58 MiB private bytes, 0 CPU seconds / 30 seconds. Browser and official client are separate costs.

## Experimental boundaries

All accounts and usage values are synthetic. Partial-failure testing deliberately fails the reserve during native retries, then restores a dummy identity after the terminal client failure. This exercises controller recovery, not production provider reset behavior. The UI separates interrupted output from the subsequent response.

Claude retains the original prompt and session ID when sent Continue, but omits incomplete assistant text from the subsequent request. The prototype uses structured CLI mode; the ordinary interactive terminal interface remains separate work.

Codex normal local-response compatibility was observed; native 429 retry and partial-response continuation were not established. Its dashboard cards are selection demonstrations only.

## Work still required for live use

Encrypted credential storage and refresh ownership, real account enrollment, actual usage fetching, real provider/account compatibility testing, tool-side-effect reconciliation, per-session routing for multiple concurrent clients, interactive terminal attachment, VS Code/desktop integrations, and memory reduction. The proxy source currently writes plaintext auth files, so this prototype offers no real-login action.

See README.md for launch instructions and experiments/*/RESULTS.md for reproducible evidence. No existing account credentials or login settings were modified.

## Native tray update

The user subsequently chose a native Windows tray UI with the local Web port retained. Launch.cmd starts `account_switcher.native`; Launch-Web.cmd preserves standalone browser mode. The native adapter reuses MIT Vitals rounded drawing and toggle patterns; upstream auth/account/updater modules are isolated. Close hides to tray; Exit/Web Shutdown ends the server and owned clients. No Windows startup registration is added.

Proxy startup is now lazy and Stop unloads it. A regression test verifies that opening the controller and swapping displayed accounts creates no proxy process. All 17 tests pass across the suite (16 full suite plus the new lazy-start check in the rerun Web tests); native visible/hidden smoke checks pass separately.

Final 30-second native sample: 62.93 MiB working set, 34.11 MiB private bytes, 0.3438 CPU seconds, no proxy or Claude session. This sample includes the visible dashboard and live local port. Earlier short samples were about 56 MiB. The original 30 MB target remains unmet; a smaller native runtime would be needed for that strict target. Native and Web account values remain synthetic.

## Local Vitals UI fork and dark/tray polish

The account view now runs Codex Vitals' actual Windows `compact_ui.AccountRow`, through `switcher_ui.SwitcherAccountRow` on the local `account-switcher` branch. Upstream row geometry, metric bars/countdowns, alias truncation, hover feedback and active state are reused. The adapter removes extra account menus and source chips, supports both providers, and maps our controller snapshots into Vitals' data models. The shell and tray popup remain custom. Original MIT attribution and upstream source are retained. No GitHub fork has been published.

The native and Web surfaces use #1e1e1e, Vitals' bundled provider logos, simplified controls and collapsed recovery tools. Windows tray hover supplies a standard status tooltip; click opens a compact account panel. The loopback Web port remains available while the native host runs.

Verification: the full existing 17-test suite passed; three new tests passed for UI import isolation, tray monitor placement and selected-account quota text. The five Web tests were rerun with PNG asset checks and passed. Native smoke checks passed for both visible and tray startup, real Vitals row action, shared HTTP state, popup construction and shutdown. Browser inspection confirmed dark rendering, logo loading and manual selection. Native tray gestures and visual positioning still need direct human visual review. JavaScript syntax check passed. The previous resource measurement remains the latest benchmark; the 30 MB target is still unresolved. All displayed account data remains synthetic.

## Native motion and Windows styling

Added shared finite Motion and FluentButton controls, original app icon assets, Windows DWM frame styling, tray fade/slide entry and reversible exit, active-row transitions, and session-tools expansion. The app remains Tk with actual Vitals rows. No embedded browser or runtime dependency was added. Real login and usage integration remain unfinished.

Verified 25 tests and native smoke checks. Programmatic native interaction confirmed complete entry, rapid exit/entry reversal, successful DWM settings and account swap through the new button. Native screenshots reviewed; footer clipping was corrected by using a 600px minimum dashboard height. Idle preview sample: 58.82 MiB working set / 32.82 MiB private bytes / 0 CPU seconds over 10 seconds, both native surfaces open and no client/proxy. GPU use and actual Windows tray gestures remain unmeasured in this pass.
