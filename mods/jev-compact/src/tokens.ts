import type { Message } from './types.ts'

/**
 * Estimated tokens for a text without a tokenizer: 3.2 characters per token, rounded up. Kept a
 * little high on purpose (cc-mod-jev calibrated it against the `input_tokens` Jev reports for
 * JSON state); for the transcript itself it is an estimate, and the benchmark reports ratios.
 */
export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 3.2)
}

/** The characters one message carries: its text, tool inputs and tool results. */
export function messageChars(message: Message): number {
  let chars = message.text.length
  for (const use of message.toolUses) chars += JSON.stringify(use.input ?? {}).length
  for (const result of message.toolResults ?? []) chars += result.text.length
  return chars
}

export function transcriptChars(messages: readonly Message[]): number {
  let chars = 0
  for (const message of messages) chars += messageChars(message)
  return chars
}
