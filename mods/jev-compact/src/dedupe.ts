// Lines an older output shares with a newer one. A coding session views the same code again and
// again (`sed -n 80,200p`, then `sed -n 100,180p`, then a Read): the older copies of those lines
// add nothing while a newer copy stays in the conversation, so they fold into a note. Nothing is
// lost by construction: every folded line is still there, in the newer output.

import { NOTE_TAG } from './apply.ts'
import type { Decision, ToolCall } from './types.ts'

const MIN_LINE = 12      // shorter lines ("}", "return x") repeat by chance; never folded
const MIN_FOLD = 300     // fold only when it saves at least this many characters
const MIN_SHARE = 0.25   // ...and at least this share of the output

/** A line as it reads without a viewer's line number (`  12→`, `12:`, `path/file.py:12:`). */
export function lineKey(line: string): string {
  return line.replace(/^\s*(?:[\w./-]+:)?\d+(?:[:\t→|-]|\s{2,})/, '').trim()
}

export function foldNote(n: number): string {
  return `${NOTE_TAG}: ${n} line${n === 1 ? '' : 's'} shown again in a later output]`
}

/** `text` with the lines `seen` already holds folded into notes, or null when that saves too little. */
export function fold(text: string, seen: ReadonlySet<string>): string | null {
  const lines = text.split('\n')
  const shared = lines.map((line) => {
    const key = lineKey(line)
    return key.length >= MIN_LINE && !key.startsWith(NOTE_TAG) && seen.has(key)
  })
  const saved = lines.reduce((sum, line, i) => sum + (shared[i] ? line.length + 1 : 0), 0)
  if (saved < MIN_FOLD || saved < text.length * MIN_SHARE) return null
  const out: string[] = []
  let run = 0
  lines.forEach((line, i) => {
    if (shared[i]) {
      run += 1
      return
    }
    if (run) out.push(foldNote(run))
    run = 0
    out.push(line)
  })
  if (run) out.push(foldNote(run))
  return out.join('\n')
}

/**
 * Folds, newest output first, the lines each older output shares with what newer outputs still
 * show. `results` holds each call's output as the compaction left it (kept, trimmed or a note) and
 * is updated in place; returns the ids of the calls folded. Outputs never changed: pinned ones
 * (first and newest messages, calls in flight) and errors; they still count as shown.
 */
export function dedupe(calls: readonly ToolCall[], decisions: readonly Decision[], results: Map<string, string>): Set<string> {
  const action = new Map(decisions.map((d) => [d.id, d.action] as const))
  const seen = new Set<string>()
  const folded = new Set<string>()
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    const call = calls[i]!
    const text = results.get(call.tool_use_id) ?? call.resultText
    const act = action.get(call.id)
    if (!call.isError && (act === 'keep' || act === 'trim' || act === 'small')) {
      const shorter = fold(text, seen)
      if (shorter !== null) {
        results.set(call.tool_use_id, shorter)
        folded.add(call.id)
      }
    }
    for (const line of (results.get(call.tool_use_id) ?? text).split('\n')) {
      const key = lineKey(line)
      if (key.length >= MIN_LINE && !key.startsWith(NOTE_TAG)) seen.add(key)
    }
  }
  return folded
}
