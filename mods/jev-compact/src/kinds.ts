// Which outputs are cheap to get again. A file's contents, a listing or a search can be read again
// from disk at the cost of one tool call; a test run, a web page, an agent's report or the output
// of something that changed state cannot (or not the same). Cheap outputs get a lower bar to be
// trimmed or replaced by a note. The idea (classify, then prune exploration first) follows
// HAR5HA-7663/jev-compact; this is a separate, smaller implementation.

import type { ToolCall } from './types.ts'

const READ_TOOLS = new Set(['Read', 'Glob', 'Grep', 'LS', 'NotebookRead'])

// Commands that only read. Unknown means not cheap: a miss costs a little saving, a wrong guess
// could cut the only record of something that can't be had again.
const READS = new Set(['cat', 'head', 'tail', 'less', 'grep', 'egrep', 'rg', 'ls', 'find', 'fd', 'wc', 'sort', 'uniq',
  'cut', 'tr', 'nl', 'column', 'stat', 'file', 'tree', 'du', 'diff', 'jq', 'which', 'type', 'pwd', 'cd', 'echo',
  'printf', 'true', 'basename', 'dirname', 'realpath', 'readlink', 'Get-Content', 'Get-ChildItem', 'Select-String'])
const GIT_READS = new Set(['log', 'show', 'diff', 'status', 'blame', 'grep', 'ls-files', 'branch', 'rev-parse', 'remote'])

/** True when every command in a shell line only reads (no writes, no scripts, no network). */
export function readOnlyShell(command: string): boolean {
  const line = command
    .replace(/'[^']*'|"(?:\\.|[^"\\])*"/g, 'Q') // quoted patterns and paths: their | and > are text
    .replace(/\d?>&?\s*\/dev\/null|2>&1/g, ' ')
  if (/>|<<|`|\$\(/.test(line)) return false // a redirect into a file, a here-document, a subshell
  return line.split(/&&|\|\||[;|\n]/).every((segment) => {
    const words = segment.trim().split(/\s+/).filter((w) => !/^[A-Za-z_][A-Za-z0-9_]*=/.test(w)) // VAR=... first
    const [verb, sub] = words
    if (!verb) return true
    if (verb === 'git') return sub !== undefined && GIT_READS.has(sub) && !/\s-[dDmM]\b/.test(segment)
    if (verb === 'sed') return !/\s-i/.test(segment)
    if (verb === 'find') return !/-(exec|delete|ok)/.test(segment)
    return READS.has(verb)
  })
}

/** True when the call only read files or listed or searched them. */
export function cheapToRedo(call: ToolCall): boolean {
  if (READ_TOOLS.has(call.tool)) return true
  if (call.tool !== 'Bash' && call.tool !== 'PowerShell') return false
  const command = String(call.input['command'] ?? '')
  return command.trim().length > 0 && readOnlyShell(command)
}
