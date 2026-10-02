export type Piece = { t: string; c: string | null }

declare module 'claude-code' {
  interface PluginState {
    'limit-status': { line: Piece[] | null }
  }
}
