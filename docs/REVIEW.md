# Code review — 2026-09-26

Scope: everything outside the vendored `proxy-fork/` and `codex-vitals-source/` trees, checked against `ACCOUNT_SWITCHER_TECHNICAL_PLAN.md` and the reference screenshots (quota card grid, Codex Vitals, PowerToys Settings).

## Verdict

The safety thinking is good: explicit AFK opt-in, dedup of failure events, bounded retries, lazy proxy start, and loopback auth with Host/Origin checks. The recovery lab shows a real Claude CLI losing its quota mid-response, moving to the reserve account and finishing the turn.

**The core product doesn't exist yet.** Every account, percentage and reset time is synthetic. Nothing reads real usage, nothing signs in, and AFK only works inside a tool-free `claude -p` stream-json session that the app starts itself, not in the terminal you actually work in. That's fine for a prototype. But the next milestone should be real data, before any more UI or harness work.

## What's solid

- `core.Router` / `core.Recovery` are small and testable, and they're the right shape for the plan's state machine.
- The Web layer is pure stdlib, long-polls (no timers), uses a per-launch token and a strict CSP, and shuts itself down when no one's connected.
- The proxy starts on Run and stops on Stop, so the dashboard costs nothing extra until you use it.
- Tests cover routing, recovery gating, Web auth and lifecycle.

## Gaps, in priority order

1. **No real usage data.** You already vendor what you need:
   - `proxy-fork/internal/api/handlers/management/plugin_quota.go` exposes `/quota/fetch` and `/quota/providers`, and `internal/runtime/executor/helps/codex_quota.go` parses Codex windows. The first reference screenshot (auth files named `claude-…@….json` with 5-hour / 7-day / 7-day-model bars) looks like this proxy's own management UI, so it can probably produce exactly that data.
   - `codex-vitals-source/windows/codexvitals_windows/codex_api.py` (`_fetch_usage`, `_normalize_window_roles`) is a working Codex usage client.
   - `core.Account.windows()` now returns a provider-neutral window list (5-hour, weekly, plus any per-model caps). Feed it real data and the UI renders it unchanged.
2. **Credentials.** The proxy writes plaintext auth files (plan §5). Put DPAPI-backed storage in place before real login lands. Until then, keep the "no real login" boundary.
3. **AFK in your real session.** Today it only works in `client.ClaudeSession` (structured, tool-free, `--tools ""`, fixed system prompt). Continuing *your* interactive Claude Code session needs the ConPTY supervisor or hook approach from plan §9. This is the hardest remaining piece and the one that makes the product. Start it early.
4. **Codex is display-only.** Swapping it changes a label and nothing else.
5. **Hardcoded model.** `claude-sonnet-4-6` appears in both `client.py` and `experiments/proxy/runtime.py`.
6. **The proxy adapter only handles accounts "A" and "B".** `proxy_demo.py` maps accounts with `account.id[-1].upper()`, so a third account breaks it.
7. **Product code imports `experiments/`.** `web.py` → `proxy_demo.py` → `experiments.proxy.runtime`. Move the runtime into the package once it stops being an experiment.
8. ~~Two UIs, one controller.~~ Resolved: tray + web dashboard only; the Tk UI is gone.

## Bugs and smaller issues

| Where | Issue | Status |
|---|---|---|
| `core.Router.fallback` | Failed over to the *first* eligible account in list order, which could be one that's nearly spent | **Fixed**: now picks the most headroom |
| `native.Dashboard.poll` | Tk dashboard re-read state every 500 ms while visible and every 2 s while hidden, forever | **Fixed**, then superseded: Tk UI removed; the tray sleeps until state changes (0 idle wake-ups measured) |
| `Launch*.cmd` | Hardcoded `%LocalAppData%\Programs\Python\Python313\pythonw.exe` | **Fixed**: falls back to `pyw`/`pythonw` on PATH |
| `web.Controller.action` | `self.pending` is set outside the lock; `ProxyDemoGateway.arm/reset` mutate state without `self.lock` | Open, low risk (operations are serialized) |
| `web.make_server` | Idle-watch thread woke every 5 s even when the timeout was off | **Fixed**: no thread without a timeout |
| Repo size | ~1,600 vendored proxy files plus the whole Vitals repo (including the Swift macOS app) to use about 5 Python files | Consider trimming to what's used |
| Docs | `README`/`IMPLEMENTATION_STATUS` were long and partly contradictory | **Fixed**: rewritten short and current |

Policy note from your own plan (§12): Anthropic and OpenAI terms restrict third-party handling of subscription credentials and rate-limit circumvention. Weigh that before running this against real accounts.

## Resource use

| Component | Measured | When it runs |
|---|---|---|
| Tray app, idle (backend included) | ~31 MB RSS, 3 threads, 0 CPU ticks, 0 context switches over 30 s (Linux, Xvfb) | While the app is open |
| Go proxy + Claude CLI | Not measured here | Only between Run and Stop |
| Browser tab | Usually more than all of the above combined | Only while the tab is open |

Changes in this pass:

- Tray: no timers or polling; one thread blocks on the controller until state changes.
- Web: all motion is finite (transitions and one-shot keyframes). There are no infinite animations. The only timer is a once-a-minute countdown tick that stops while the tab is hidden. The DOM is built once and patched in place.

## UI direction

The Web UI has been rebuilt around the references:

- A Fluent-style dark top bar.
- "In use" tiles per provider, with a headroom ring and the next reset.
- An automation card.
- Account cards per provider, each showing every usage window (including per-model caps like "Weekly · Fable"), plan chip, email and reset time in absolute and relative form.
- Swap buttons in the provider's accent colour.

Motion is deliberately restrained: short ease-out fades, no bounce, no glow; toasts for swaps, failovers and errors. The Recovery lab expands smoothly and shows an activity timeline. Dark theme only, kept deliberately minimal; it respects reduced-motion settings.

Decision (2026-09-26): the tray is the app; left-click opens this dashboard in an app window. Tk was removed. If one always-open, tiny, premium window is ever needed, the path is a native WinUI/Win32 shell. Do that only after real data and real-session AFK work.

## Suggested next steps

1. Real usage: wire the proxy's quota endpoints (or the Vitals Codex client) into `Account.windows()`.
2. Encrypted credential store plus real sign-in for one provider.
3. Interactive-session AFK for the Claude CLI (ConPTY or hooks).
4. ~~Drop the Tk dashboard.~~ Done: the app is now tray-only, with the web dashboard opened from the tray.
