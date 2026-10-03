// One Jev compaction: pin what must never change, stub what a later call superseded, ask Jev
// about the rest in as few requests as fit, and rebuild the transcript from the answers.
//
// The flow (state fitting, batching) follows cc-mod-jev (MIT, Jay W); the decisions differ: see
// DecisionAction in types.ts and THIRD-PARTY-NOTICES.txt.

import { applyEdits, editsFor } from './apply.ts'
import { dedupe } from './dedupe.ts'
import { cheapToRedo } from './kinds.ts'
import { noulOf, questionFor, questionName } from './questions.ts'
import { fitState, goalOf } from './state.ts'
import { estimateTokens, transcriptChars } from './tokens.ts'
import { collectToolCalls, pairChars, supersededCalls } from './transcript.ts'
import type { CompactOptions, CompactResult, CompactStats, Decision, JevAnswer, JevAsker, JevQuestions, Message, ToolCall } from './types.ts'

export const DEFAULT_OPTIONS: CompactOptions = {
  keepThreshold: 0.55,
  stubThreshold: 0.35,
  cheapKeepThreshold: 0.62,
  cheapStubThreshold: 0.45,
  preserveRecentMessages: 8,
  minPairChars: 400,
  trimHeadChars: 900,
  trimTailChars: 400,
  maxWriteInputChars: 600,
  maxStateTokens: 20000,
  maxRequestTokens: 28000,
  dedupe: true,
}

const REQUEST_OVERHEAD_TOKENS = 200

function finite(value: unknown, fallback: number, min = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(min, value) : fallback
}

export function resolveOptions(options: Partial<CompactOptions> = {}): CompactOptions {
  const d = DEFAULT_OPTIONS
  const keepThreshold = Math.min(1, finite(options.keepThreshold, d.keepThreshold))
  const resolved: CompactOptions = {
    keepThreshold,
    stubThreshold: Math.min(keepThreshold, finite(options.stubThreshold, d.stubThreshold)),
    cheapKeepThreshold: Math.min(1, finite(options.cheapKeepThreshold, d.cheapKeepThreshold)),
    cheapStubThreshold: Math.min(1, finite(options.cheapStubThreshold, d.cheapStubThreshold)),
    preserveRecentMessages: Math.floor(finite(options.preserveRecentMessages, d.preserveRecentMessages)),
    minPairChars: finite(options.minPairChars, d.minPairChars),
    trimHeadChars: Math.floor(finite(options.trimHeadChars, d.trimHeadChars)),
    trimTailChars: Math.floor(finite(options.trimTailChars, d.trimTailChars)),
    maxWriteInputChars: Math.floor(finite(options.maxWriteInputChars, d.maxWriteInputChars, 200)),
    maxStateTokens: finite(options.maxStateTokens, d.maxStateTokens, 1),
    maxRequestTokens: finite(options.maxRequestTokens, d.maxRequestTokens, 1),
    dedupe: options.dedupe ?? d.dedupe,
  }
  if (options.goal) resolved.goal = options.goal
  return resolved
}

/** The candidates in batches whose questions, beside the whole state, fit one request; throws when one alone does not. */
export function batchCalls(calls: readonly ToolCall[], stateTokens: number, maxRequestTokens: number): ToolCall[][] {
  const budget = maxRequestTokens - stateTokens - REQUEST_OVERHEAD_TOKENS
  const batches: ToolCall[][] = []
  let current: ToolCall[] = []
  let used = 0
  for (const call of calls) {
    const tokens = estimateTokens(JSON.stringify(questionFor(call)))
    if (tokens > budget) throw new Error(`the question for ${call.id} does not fit beside the state within ${maxRequestTokens} tokens`)
    if (current.length > 0 && used + tokens > budget) {
      batches.push(current)
      current = []
      used = 0
    }
    current.push(call)
    used += tokens
  }
  if (current.length > 0) batches.push(current)
  return batches
}

/**
 * The action for one asked call from Jev's probability. Outputs cheap to get again (file reads and
 * searches) need a higher one to stay whole. A trim that would cut nothing keeps the output whole.
 */
export function decide(call: ToolCall, need: number, options: CompactOptions): Decision {
  const base = { id: call.id, tool: call.tool, need, chars: pairChars(call) }
  const cheap = cheapToRedo(call) // a file read or search: one tool call gets it back
  if (need >= (cheap ? options.cheapKeepThreshold : options.keepThreshold)) return { ...base, action: 'keep' }
  if (need >= (cheap ? options.cheapStubThreshold : options.stubThreshold)) {
    const fits = call.resultChars <= options.trimHeadChars + options.trimTailChars
    return { ...base, action: fits ? 'keep' : 'trim' }
  }
  return { ...base, action: 'stub' }
}

function newStats(messages: readonly Message[], calls: readonly ToolCall[]): CompactStats {
  const chars = transcriptChars(messages)
  const tokens = Math.ceil(chars / 3.2)
  return {
    messagesBefore: messages.length, messagesAfter: messages.length, charsBefore: chars, charsAfter: chars,
    tokensBefore: tokens, tokensAfter: tokens, calls: calls.length, pinned: 0, small: 0, superseded: 0,
    asked: 0, kept: 0, trimmed: 0, stubbed: 0, folded: 0, stateTokens: 0, stateStage: 0, requests: 0, jevInputTokens: 0, jevCostUsd: 0,
  }
}

/**
 * The transcript with stale tool outputs shrunk, everything else verbatim. Never touched: every
 * user and assistant text, the first message, the newest `preserveRecentMessages`, calls in
 * flight, and every error output (what went wrong is what the model must not repeat).
 * Throws on a Jev failure, a malformed answer, or a state that cannot fit.
 */
export async function compact(messages: readonly Message[], asker: JevAsker, partial: Partial<CompactOptions> = {}): Promise<CompactResult> {
  const options = resolveOptions(partial)
  const calls = collectToolCalls(messages, options.preserveRecentMessages)
  const stats = newStats(messages, calls)
  const superseded = supersededCalls(calls)
  const decisions: Decision[] = []
  const candidates: ToolCall[] = []
  for (const call of calls) {
    const chars = pairChars(call)
    if (call.pinned || call.isError) {
      stats.pinned += 1
      decisions.push({ id: call.id, tool: call.tool, action: 'pinned', chars })
    } else if (superseded.has(call.id)) {
      stats.superseded += 1
      decisions.push({ id: call.id, tool: call.tool, action: 'superseded', chars })
    } else if (call.resultChars < options.minPairChars) { // nothing to gain on the output (a Write's long input is still shortened)
      stats.small += 1
      decisions.push({ id: call.id, tool: call.tool, action: 'small', chars })
    } else {
      candidates.push(call)
    }
  }
  stats.asked = candidates.length

  if (candidates.length > 0) {
    const fitted = fitState(messages, calls, goalOf(messages, options.goal), options.maxStateTokens)
    stats.stateTokens = fitted.tokens
    stats.stateStage = fitted.stage
    const batches = batchCalls(candidates, fitted.tokens, options.maxRequestTokens)
    const responses = await Promise.all(batches.map((batch) => {
      const questions: JevQuestions = {}
      for (const call of batch) Object.assign(questions, questionFor(call))
      return asker.ask(fitted.state, questions)
    }))
    const answers: Record<string, JevAnswer> = {}
    for (const response of responses) {
      Object.assign(answers, response.answers)
      stats.jevInputTokens += response.usage?.input_tokens ?? 0
      stats.jevCostUsd += response.usage?.cost ?? 0
    }
    stats.requests = batches.length
    for (const call of candidates) {
      const decision = decide(call, noulOf(answers, questionName(call)), options)
      decisions.push(decision)
      if (decision.action === 'keep') stats.kept += 1
      else if (decision.action === 'trim') stats.trimmed += 1
      else stats.stubbed += 1
    }
  }
  decisions.sort((a, b) => Number(a.id.slice(1)) - Number(b.id.slice(1)))

  const edits = editsFor(calls, decisions, options)
  if (options.dedupe) {
    // Lines a newer output shows again fold out of the older one: nothing is lost (see dedupe.ts)
    const results = new Map(calls.map((call) => [call.tool_use_id, edits.get(call.tool_use_id)?.result ?? call.resultText] as const))
    for (const id of dedupe(calls, decisions, results)) {
      const call = calls.find((c) => c.id === id)!
      edits.set(call.tool_use_id, { ...edits.get(call.tool_use_id), result: results.get(call.tool_use_id)! })
      stats.folded += 1
    }
  }
  const output = applyEdits(messages, edits)
  stats.messagesAfter = output.length
  stats.charsAfter = transcriptChars(output)
  stats.tokensAfter = Math.ceil(stats.charsAfter / 3.2)
  return { messages: output, decisions, stats }
}

/** The fraction of characters removed, 0 to 1. */
export function reductionRatio(result: CompactResult): number {
  const { charsBefore, charsAfter } = result.stats
  return charsBefore === 0 ? 0 : (charsBefore - charsAfter) / charsBefore
}

/** One line for the log and the app: what changed and what it cost. */
export function summarize(result: CompactResult): string {
  const s = result.stats
  return `${Math.round(reductionRatio(result) * 100)}% smaller (~${Math.round(s.tokensBefore / 1000)}k to ~${Math.round(s.tokensAfter / 1000)}k tokens); ` +
    `${s.asked} asked: ${s.kept} kept, ${s.trimmed} trimmed, ${s.stubbed} stubbed; ${s.folded} folded, ${s.superseded} superseded, ${s.pinned} pinned, ${s.small} small; ` +
    `${s.requests} Jev request(s), $${s.jevCostUsd.toFixed(5)}`
}
