import type { Register } from 'claude-code'

// Switching accounts means the next account reads the whole session uncached (prompt caches are per
// account). Compacting first, on the account that still has the session cached, makes that read small.
// A compaction itself needs quota, so it starts well before the limit, once per 5-hour window.
export const register: Register = (on, options) => {
  const trigger = Number(options.triggerPercent ?? 90)
  const minTokens = Number(options.minTokens ?? 150_000)
  const instructions = String(options.instructions ?? '')
  let doneFor: string | undefined // the window (its reset time) this session was last compacted in
  let running = false

  on('session.measure', async ($, e, next) => {
    const result = await next(e)
    const window = e.rateLimits.find(w => w.kind === 'five_hour')
    const tokens = e.context.tokens ?? 0
    if (!window || running || window.percentUsed < trigger || tokens < minTokens) return result
    const key = window.resetsAt ?? 'unknown'
    if (doneFor === key) return result
    running = true
    try {
      const { skip } = await $.session.compact(instructions ? { instructions } : undefined)
      if (skip === undefined) {
        doneFor = key
        $.ui.toast(`Compacted ~${Math.round(tokens / 1000)}k tokens before the 5-hour limit (${window.percentUsed}% used)`)
      }
    } catch {
      // A turn is still running: the next measurement tries again.
    } finally {
      running = false
    }
    return result
  })
}
