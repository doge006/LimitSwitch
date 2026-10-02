import { expect, test } from 'claude-code/testing'

const measure = (percent: number, tokens: number, resetsAt = '2026-10-02T22:00:00Z', kind = 'five_hour') => ({
  context: { window: 1_000_000, tokens, percent: Math.round(tokens / 10_000) },
  rateLimits: [{ kind, percentUsed: percent, resetsAt }],
  changed: ['rateLimits' as const],
})

function counting(on: any) {
  const seen = { compacts: 0, instructions: undefined as string | undefined }
  on('session.measure', (_$: unknown, e: unknown) => ({ changed: (e as { changed: string[] }).changed }))
  on('session.compact', (_$: unknown, e: { instructions?: string }) => {
    seen.compacts++
    seen.instructions = e.instructions
    return { messages: [{ role: 'user', text: 'summary', toolUses: [] }] }
  })
  return seen
}

test('compacts a large session at the end of the 5-hour window, once per window', async ($, on) => {
  const seen = counting(on)
  await $.session.measure(measure(97.5, 700_000))
  expect(seen.compacts).toBe(1)
  expect(seen.instructions).toContain('current task')
  await $.session.measure(measure(99, 700_000)) // same window: not again
  expect(seen.compacts).toBe(1)
  await $.session.measure(measure(98, 700_000, '2026-10-03T03:00:00Z')) // the next window
  expect(seen.compacts).toBe(2)
})

test('the weekly window counts too', async ($, on) => {
  const seen = counting(on)
  await $.session.measure(measure(98, 700_000, '2026-10-08T00:00:00Z', 'seven_day'))
  expect(seen.compacts).toBe(1)
})

test('leaves small sessions and low usage alone', async ($, on) => {
  const seen = counting(on)
  await $.session.measure(measure(99, 200_000)) // 20% of the window: cheap to load elsewhere
  await $.session.measure(measure(90, 700_000)) // not at the end yet
  await $.session.measure({ context: { window: 1_000_000, tokens: 700_000, percent: 70 }, rateLimits: [], changed: ['context' as const] })
  expect(seen.compacts).toBe(0)
})

test('tries again when a compaction did not go through', async ($, on) => {
  let calls = 0
  on('session.measure', (_$: unknown, e: { changed: string[] }) => ({ changed: e.changed }))
  on('session.compact', () => {
    calls++
    if (calls === 1) throw new Error('a turn is running')
    return { messages: [{ role: 'user', text: 'summary', toolUses: [] }] }
  })
  await $.session.measure(measure(98, 700_000))
  await $.session.measure(measure(99, 700_000))
  await $.session.measure(measure(99, 700_000)) // done now: not again
  expect(calls).toBe(2)
})

test('the thresholds are settings', { options: { triggerPercent: 70, minContextPercent: 1 } }, async ($, on) => {
  const seen = counting(on)
  await $.session.measure(measure(72, 20_000))
  expect(seen.compacts).toBe(1)
})
