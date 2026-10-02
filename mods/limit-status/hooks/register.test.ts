import { expect, test } from 'claude-code/testing'

test('measuring passes through, and does nothing without LimitSwitcher to talk to', async ($, on) => {
  on('session.measure', (_$: unknown, e: { changed: string[] }) => ({ changed: e.changed }))
  const result = await $.session.measure({
    context: { window: 1_000_000, tokens: 200_000, percent: 20 },
    rateLimits: [{ kind: 'five_hour', percentUsed: 41, resetsAt: '2026-10-02T22:00:00Z' }],
    changed: ['rateLimits' as const],
  })
  expect(result.changed).toEqual(['rateLimits'])
})
