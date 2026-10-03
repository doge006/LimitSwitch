// What Jev is asked about each call. One `noul` question per call (cc-mod-jev asks two: whether
// the call and whether its output should stay); calls are never removed here, so only the output's
// question matters, which halves the question tokens.

import type { JevAnswer, JevQuestions, ToolCall } from './types.ts'

export function questionName(call: ToolCall): string {
  return `need_${call.id}`
}

/** Phrased as a statement, so a high probability means "keep". */
export function questionFor(call: ToolCall): JevQuestions {
  return {
    [questionName(call)]: {
      type: 'noul',
      instructions:
        `The exact output of tool call ${call.id} (${call.tool}, ${call.resultChars} chars) must stay in the history ` +
        'verbatim for the assistant to finish the goal well: it holds details the assistant will refer back to ' +
        '(code it is editing, values, paths, errors, decisions) and that it could not cheaply get again.',
      criteria: {
        true: 'Still needed: later steps depend on its exact contents, or it is the latest view of something still being worked on.',
        false: 'No longer needed: it was acted on and finished with, a later call superseded it, it is unrelated to the goal, or re-running the tool would give it again.',
      },
    },
  }
}

/** The probability in one answer; throws when it is missing or malformed. */
export function noulOf(answers: Record<string, JevAnswer>, name: string): number {
  const answer = answers[name]
  if (!answer || typeof answer.noul !== 'number' || !Number.isFinite(answer.noul)) {
    throw new Error(`Jev answer missing or malformed for ${name}`)
  }
  return answer.noul
}
