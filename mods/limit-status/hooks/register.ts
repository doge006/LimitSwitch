import type { Register } from 'claude-code'

// The usage LimitSwitcher follows, straight from Claude Code after every turn (and whenever a
// window moves a point), instead of through a status line command. LimitSwitcher answers with the
// line to show, which this mod pins under the prompt in its own slot: the person's own status line
// is never touched.
const EVERY = 30_000 // an idle session still shows the app's freshest numbers

type Window = { kind: string; percentUsed: number; resetsAt?: string }
type Context = { tokens?: number; percent?: number }

function tokensText(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
}

async function report($: any, statePath: string, rateLimits: readonly Window[], context: Context): Promise<void> {
  if (!statePath) return
  let base = ''
  let token = ''
  try {
    const state = JSON.parse(await $.fs.read(statePath))
    base = String(state.url).replace(/\/api\/afk$/, '')
    token = String(state.token)
  } catch {
    return // LimitSwitcher is not running
  }
  const limits: Record<string, { used_percentage: number; resets_at?: number }> = {}
  for (const w of rateLimits) {
    if (w.kind !== 'five_hour' && w.kind !== 'seven_day') continue
    limits[w.kind] = { used_percentage: w.percentUsed, ...(w.resetsAt ? { resets_at: Math.floor(Date.parse(w.resetsAt) / 1000) } : {}) }
  }
  try {
    const reply = await $.http.fetch(`${base}/api/statusline`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ rate_limits: Object.keys(limits).length ? limits : null, session: String(await $.session.id()), source: 'mod' }),
    })
    const line = reply.ok ? JSON.parse(reply.text).line : null
    const left = context.percent !== undefined ? ` · ${Math.max(0, 100 - context.percent)}% left` : ''
    const extra = context.tokens ? ` · ctx ${tokensText(context.tokens)}${left}` : ''
    $.ui.status(typeof line === 'string' && line ? line + extra : undefined)
  } catch {
    $.ui.status(undefined)
  }
}

export const register: Register = (on, options) => {
  const statePath = String(options.statePath ?? '')
  let last = '' // what was last sent: nothing new, nothing to ask again

  on('session.measure', async ($, e, next) => {
    const result = await next(e)
    const key = JSON.stringify([e.rateLimits, e.context.tokens])
    if (key !== last) {
      last = key
      await report($, statePath, e.rateLimits, e.context)
    }
    return result
  })

  on('session.start', async ($, e, next) => {
    const result = await next(e)
    const poll = async () => {
      const { rateLimits, context } = await $.session.usage()
      await report($, statePath, rateLimits, context)
    }
    void poll() // never holds the session's start
    $.clock.every(EVERY, poll)
    return result
  })
}
