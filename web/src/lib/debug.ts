// Read-only hooks used by the Playwright end-to-end checks (window.__lab.state()).
export interface DebugState {
  [k: string]: unknown
}
const providers = new Map<string, () => unknown>()
export function registerDebug(key: string, fn: () => unknown): () => void {
  providers.set(key, fn)
  return () => { if (providers.get(key) === fn) providers.delete(key) }
}
export function installDebug(): void {
  ;(window as unknown as { __lab: unknown }).__lab = {
    state: () => Object.fromEntries([...providers.entries()].map(([k, f]) => [k, f()])),
  }
}
