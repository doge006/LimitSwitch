import { expect, test } from 'claude-code/testing'

const measure = (percent: number, tokens: number, resetsAt = '2026-10-02T22:00:00Z') => ({
  context: { window: 1_000_000, tokens },
  rateLimits: [{ kind: 'five_hour', percentUsed: percent, resetsAt }],
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

test('compacts a large session near the 5-hour limit, once per window', async ($, on) => {
  const seen = counting(on)
  await $.session.measure(measure(91, 400_000))
  expect(seen.compacts).toBe(1)
  expect(seen.instructions).toContain('current task')
  await $.session.measure(measure(95, 400_000)) // same window: not again
  expect(seen.compacts).toBe(1)
  await $.session.measure(measure(91, 400_000, '2026-10-03T03:00:00Z')) // the next window
  expect(seen.compacts).toBe(2)
})

test('leaves small sessions and low usage alone', async ($, on) => {
  const seen = counting(on)
  await $.session.measure(measure(97, 50_000)) // cheap to load elsewhere
  await $.session.measure(measure(40, 600_000)) // far from the limit
  await $.session.measure({ context: { window: 1_000_000, tokens: 600_000 }, rateLimits: [], changed: ['context' as const] })
  expect(seen.compacts).toBe(0)
})

test('the thresholds are settings', { options: { triggerPercent: 70, minTokens: 10_000 } }, async ($, on) => {
  const seen = counting(on)
  await $.session.measure(measure(72, 20_000))
  expect(seen.compacts).toBe(1)
})
