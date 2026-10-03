// Masks what looks like a credential before anything leaves the machine: Jev reads the state
// built from the conversation, and a session can hold keys a person pasted or a tool printed.
// No pattern matches a quote or a backslash, so masking a JSON text keeps it valid JSON.

const PATTERNS: readonly [RegExp, string][] = [
  [/\bsk-[A-Za-z0-9_-]{16,}/g, 'sk-[masked]'],                                   // OpenAI, Anthropic, OpenRouter
  [/\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}/g, '[masked GitHub token]'],
  [/\bxox[abprs]-[A-Za-z0-9-]{10,}/g, '[masked Slack token]'],
  [/\bAKIA[0-9A-Z]{16}\b/g, '[masked AWS key]'],
  [/\bAIza[0-9A-Za-z_-]{30,}/g, '[masked Google key]'],
  [/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g, '[masked JWT]'],
  [/(\bBearer\s+)[A-Za-z0-9._~+/-]{16,}=*/gi, '$1[masked]'],
  [/(\b[A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z0-9_]*\s*[=:]\s*)[^\s"'\\,;]{8,}/gi, '$1[masked]'],
  [/(:\/\/[^/\s:@"\\]+:)[^@\s"/\\]+@/g, '$1[masked]@'],                            // passwords in URLs
]

export function maskSecrets(text: string): string {
  let out = text
  for (const [pattern, replacement] of PATTERNS) out = out.replace(pattern, replacement)
  return out
}
