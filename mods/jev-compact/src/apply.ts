// Rebuilds the transcript from the decisions. Nothing is summarised and nothing is removed whole:
// an output is kept, trimmed to its head and tail, or replaced by a one-line note, and the long
// inputs of file-writing tools and scripts are shortened (what they did is on disk). Every changed spot
// says so, so the model re-runs a tool instead of guessing what was there.

import type { Decision, Message, ToolCall, ToolResult, ToolUse } from './types.ts'

export const NOTE_TAG = '[LimitSwitcher'

/** Tools whose long string inputs are file contents already on disk. */
export const WRITE_TOOLS = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit'])
/**
 * Tools whose long inputs are scripts: an old one's effect is on disk (or in its output, which is
 * judged on its own). Measured on a real session: 20% of the transcript was such script inputs.
 */
export const SCRIPT_TOOLS = new Set(['Bash', 'PowerShell'])

function inputNote(tool: string): string {
  return WRITE_TOOLS.has(tool) ? 'the file on disk has them' : 'what the script did is on disk'
}

export function stubText(call: ToolCall, superseded: boolean): string {
  const why = superseded ? 'the same file is read again later in this conversation' : 'it was judged no longer needed'
  return `${NOTE_TAG} removed this ${call.tool} output (${call.resultChars} chars) before an account swap: ${why}. Re-run the tool if you need it.]`
}

export function trimText(text: string, head: number, tail: number): string {
  if (text.length <= head + tail) return text
  const omitted = text.length - head - tail
  return `${text.slice(0, head)}\n\n${NOTE_TAG} removed ${omitted} chars from the middle of this output before an account swap. Re-run the tool if you need them.]\n\n${tail > 0 ? text.slice(-tail) : ''}`
}

/** A file-writing tool's input with each string longer than `max` cut to its head and tail. */
export function shortenInput(input: Record<string, unknown>, max: number, tool = 'Write'): Record<string, unknown> | null {
  let changed = false
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === 'string' && value.length > max) {
      const head = Math.ceil(max * 0.6)
      const tail = max - head
      out[key] = `${value.slice(0, head)}\n${NOTE_TAG} removed ${value.length - max} chars of this input before an account swap; ${inputNote(tool)}.]\n${value.slice(-tail)}`
      changed = true
    } else {
      out[key] = value
    }
  }
  return changed ? out : null
}

type Edit = { result?: string; input?: Record<string, unknown> }

/** What each tool_use_id's call becomes. */
export function editsFor(
  calls: readonly ToolCall[],
  decisions: readonly Decision[],
  options: { trimHeadChars: number; trimTailChars: number; maxWriteInputChars: number },
): Map<string, Edit> {
  const byId = new Map(calls.map((call) => [call.id, call] as const))
  const edits = new Map<string, Edit>()
  for (const decision of decisions) {
    const call = byId.get(decision.id)
    if (!call || decision.action === 'pinned') continue
    const edit: Edit = {}
    if (decision.action === 'stub' || decision.action === 'superseded') {
      edit.result = stubText(call, decision.action === 'superseded')
    } else if (decision.action === 'trim') {
      const text = trimText(call.resultText, options.trimHeadChars, options.trimTailChars)
      if (text !== call.resultText) edit.result = text
    }
    if (WRITE_TOOLS.has(call.tool) || SCRIPT_TOOLS.has(call.tool)) {
      const input = shortenInput(call.input, options.maxWriteInputChars, call.tool)
      if (input) edit.input = input
    }
    if (edit.result !== undefined || edit.input !== undefined) edits.set(call.tool_use_id, edit)
  }
  return edits
}

/**
 * The transcript with the edits applied. A message nothing touched is the same object (its engine
 * handle included, so the engine keeps it whole); a changed one is rebuilt from its role, text and
 * tool blocks. The set of calls and results, and their order, never changes.
 */
export function applyEdits(messages: readonly Message[], edits: ReadonlyMap<string, Edit>): Message[] {
  if (edits.size === 0) return [...messages]
  return messages.map((message) => {
    let changed = false
    const toolUses: ToolUse[] = message.toolUses.map((use) => {
      const input = edits.get(use.tool_use_id)?.input
      if (!input) return use
      changed = true
      return { ...use, input }
    })
    const toolResults: ToolResult[] | undefined = message.toolResults?.map((result) => {
      const text = edits.get(result.tool_use_id)?.result
      if (text === undefined) return result
      changed = true
      return { tool_use_id: result.tool_use_id, text, isError: result.isError }
    })
    if (!changed) return message
    const rebuilt: Message = { role: message.role, text: message.text, toolUses }
    if (toolResults && toolResults.length > 0) rebuilt.toolResults = toolResults
    return rebuilt
  })
}
