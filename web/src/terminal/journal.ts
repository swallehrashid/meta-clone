// The Journal (drawer -> Journal): what the terminal did, in MT5's own wording, kept on this device.
export interface JournalEntry { t: number; src: 'Trades' | 'Network' | 'Terminal'; msg: string }

const KEY = 'mt5.journal.v1'
const MAX = 800
const listeners = new Set<() => void>()

function read(): JournalEntry[] {
  try { return JSON.parse(localStorage.getItem(KEY) || '[]') as JournalEntry[] } catch { return [] }
}

export function journal(): JournalEntry[] { return read() }

export function log(src: JournalEntry['src'], msg: string): void {
  const all = read()
  all.push({ t: Date.now(), src, msg })
  try { localStorage.setItem(KEY, JSON.stringify(all.slice(-MAX))) } catch { /* ignore */ }
  listeners.forEach((f) => f())
}

export function clearJournal(): void {
  try { localStorage.removeItem(KEY) } catch { /* ignore */ }
  listeners.forEach((f) => f())
}

export function onJournal(f: () => void): () => void { listeners.add(f); return () => { listeners.delete(f) } }
