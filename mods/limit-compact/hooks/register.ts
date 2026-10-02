import type { Register } from 'claude-code'

// Switching accounts means the next account reads the whole session uncached (prompt caches are per
// account). Compacting first, as the last thing the old account does while it still has the session
// cached, makes that read small. A compaction needs a little quota, so it starts near the end of the
// 5-hour or weekly window, once per window; a failed attempt is simply tried again at the next
// measurement, and costs nothing beyond what switching would have.
export const register: Register = (on, options) => {
  const trigger = Number(options.triggerPercent ?? 97)
  const minContext = Number(options.minContextPercent ?? 50)
  const instructions = String(options.instructions ?? '')
  const done = new Set<string>() // `<window>:<its reset time>` already compacted in
  let running = false

  on('session.measure', async ($, e, next) => {
    const result = await next(e)
    if (running) return result
    const tokens = e.context.tokens ?? 0
    const percent = e.context.percent ?? (e.context.window ? (tokens / e.context.window) * 100 : 0)
    if (percent < minContext) return result
    const due = e.rateLimits.filter(w => (w.kind === 'five_hour' || w.kind === 'seven_day') && w.percentUsed >= trigger)
    if (due.length === 0) return result
    const keys = due.map(w => `${w.kind}:${w.resetsAt ?? ''}`)
    if (keys.every(key => done.has(key))) return result
    running = true
    try {
      const { skip } = await $.session.compact(instructions ? { instructions } : undefined)
      if (skip === undefined) {
        for (const key of keys) done.add(key)
        const worst = Math.max(...due.map(w => w.percentUsed))
        $.ui.toast(`Compacted ~${Math.round(tokens / 1000)}k tokens at ${worst}% usage, before the limit`)
      }
    } catch {
      // A turn is still running, or the limit is already up: the next measurement tries again.
    } finally {
      running = false
    }
    return result
  })
}
