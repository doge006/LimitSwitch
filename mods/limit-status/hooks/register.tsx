import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Piece } from '../types'

// The usage LimitSwitcher follows, straight from Claude Code after every turn (and whenever a
// window moves a point), instead of through a status line command. LimitSwitcher answers with the
// line to show, which this mod draws above the prompt in colour: the person's own status line is
// never touched.
const EVERY = 30_000 // an idle session still shows the app's freshest numbers
const HOLD = 120_000 // the line stays this long when the app has nothing to show (a swap in progress, a moment busy)

let lastGoodAt = 0 // when the app last gave a line
let lastPollAt = 0 // when the band last asked for one itself

const line = atom({ plugin: 'limit-status', key: 'line' } as const, null)

// icon green, "LimitSwitcher" and the account grey, 5h / 1w blue, the numbers green with plenty
// left, yellow in the middle, red when low
const COLORS: Record<string, string> = {
  dim: '#8b9098',
  label: '#5aa9ff',
  good: '#4cc38a',
  warn: '#e5b54a',
  bad: '#e5604d',
}

type Window = { kind: string; percentUsed: number; resetsAt?: string }
type Context = { tokens?: number; percent?: number }

function tokensText(n: number): string {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}k` : String(n)
}

function contextPieces(context: Context): Piece[] {
  if (!context.tokens) return []
  const left = context.percent !== undefined ? Math.max(0, 100 - context.percent) : undefined
  const color = left === undefined ? null : left > 30 ? 'good' : left > 10 ? 'warn' : 'bad'
  const pieces: Piece[] = [{ t: ' · ', c: 'dim' }, { t: `ctx ${tokensText(context.tokens)}`, c: color }]
  if (left !== undefined) pieces.push({ t: ' · ', c: 'dim' }, { t: `${left}%`, c: color }, { t: ' left', c: 'dim' })
  return pieces
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
    const answer = reply.ok ? JSON.parse(reply.text) : null
    const parts: Piece[] | null = Array.isArray(answer?.parts) && answer.parts.length ? answer.parts : null
    const now = Number(await $.clock.now())
    if (parts) {
      lastGoodAt = now
      await update($, line, () => [...parts, ...contextPieces(context)])
    } else if (now - lastGoodAt > HOLD) {
      await update($, line, () => null) // nothing for a while: the band goes away
    }
  } catch {
    // the app is busy: what is shown stays for now
  }
}

async function poll($: any, statePath: string): Promise<void> {
  const { rateLimits, context } = await $.session.usage()
  await report($, statePath, rateLimits, context)
}

export const register: Register = (on, options) => {
  const statePath = String(options.statePath ?? '')
  let last = '' // what was last sent: nothing new, nothing to ask again

  on('session.measure', async ($, e, next) => {
    const result = await next(e)
    const key = JSON.stringify([e.rateLimits, e.context.tokens])
    if (key !== last || !(await read($, line))) { // also when nothing is shown (a /clear, a failed ask)
      last = key
      await report($, statePath, e.rateLimits, e.context)
    }
    return result
  })

  on('session.start', async ($, e, next) => {
    const result = await next(e)
    void poll($, statePath) // never holds the session's start
    $.clock.every(EVERY, () => poll($, statePath))
    return result
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const pieces = await read($, line)
    if (!pieces) {
      // nothing to draw: ask for a line now and then (after a /clear nothing else would)
      const now = Number(await $.clock.now())
      if (now - lastPollAt > 10_000) {
        lastPollAt = now
        void poll($, statePath)
      }
      return next(e)
    }
    if (e.props.hasSurvey) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box>
        {pieces.map((piece, index) => (
          <Text key={String(index)} color={piece.c ? COLORS[piece.c] : undefined}>
            {piece.t}
          </Text>
        ))}
      </Box>
    )
  })
}
