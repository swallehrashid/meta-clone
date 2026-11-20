import { mergeBars, trimBars } from './series'
import type { BackfillMsg, Bar, ServerMsg, Tick, Timeframe, UpdateMsg } from './types'

export type Status = 'connecting' | 'open' | 'closed'
export const MAX_BARS = 3000

export interface SymbolData {
  symbol: string
  tf: Timeframe
  digits: number
  pip: number
  bars: Bar[]
  lastTick: { t: number; bid: number; ask: number } | null
}
/** reset = whole series replaced (setData); bars = incremental candles (update) */
export type SymEvent = { type: 'reset' } | { type: 'bars'; bars: Bar[] }

/** Holds what one websocket (delayed OR peek) has delivered. Never merges the two. */
export class MarketStore {
  readonly data = new Map<string, SymbolData>()
  cutoffMs = 0
  delay = 0
  status: Status = 'connecting'
  error = ''
  version = 0
  private symListeners = new Map<string, Set<(e: SymEvent) => void>>()
  private anyListeners = new Set<() => void>()
  private timer: ReturnType<typeof setTimeout> | null = null

  /** Subscribe to chart-level events of one symbol. Returns an unsubscribe function. */
  onSymbol(symbol: string, fn: (e: SymEvent) => void): () => void {
    let set = this.symListeners.get(symbol)
    if (!set) this.symListeners.set(symbol, (set = new Set()))
    set.add(fn)
    return () => { set!.delete(fn) }
  }

  /** Subscribe to coalesced "something changed" notifications (for React headers). */
  subscribe = (fn: () => void): (() => void) => {
    this.anyListeners.add(fn)
    return () => { this.anyListeners.delete(fn) }
  }
  getVersion = (): number => this.version

  private bump(): void {
    this.version++
    if (this.timer) return
    this.timer = setTimeout(() => {
      this.timer = null
      this.anyListeners.forEach((f) => f())
    }, 100)
  }

  private emit(symbol: string, e: SymEvent): void {
    this.symListeners.get(symbol)?.forEach((f) => f(e))
  }

  setStatus(s: Status, error = ''): void {
    this.status = s
    this.error = error
    this.bump()
  }

  handle(msg: ServerMsg): void {
    switch (msg.type) {
      case 'backfill': return this.onBackfill(msg)
      case 'update': return this.onUpdate(msg)
      case 'heartbeat': this.cutoffMs = Math.max(this.cutoffMs, msg.cutoff_ms); this.bump(); return
      case 'error': this.error = msg.message; this.bump(); return
    }
  }

  private onBackfill(m: BackfillMsg): void {
    this.cutoffMs = m.cutoff_ms          // a backfill may legitimately move the cutoff back (delay change)
    this.delay = m.delay
    this.error = ''
    const lt = m.last_tick
    const d: SymbolData = {
      symbol: m.symbol, tf: m.tf, digits: m.digits, pip: m.pip,
      bars: m.candles.map((c) => ({ ...c })),
      lastTick: lt ? { t: lt[0], bid: lt[1], ask: lt[2] } : null,
    }
    this.data.set(m.symbol, d)
    this.emit(m.symbol, { type: 'reset' })
    this.bump()
  }

  private onUpdate(m: UpdateMsg): void {
    this.cutoffMs = Math.max(this.cutoffMs, m.cutoff_ms)
    for (const [sym, u] of Object.entries(m.symbols)) {
      const d = this.data.get(sym)
      if (!d) continue                   // update for a symbol we have no backfill for yet
      const applied = mergeBars(d.bars, u.candles)
      trimBars(d.bars, MAX_BARS)
      const t: Tick | undefined = u.ticks[u.ticks.length - 1]
      if (t) d.lastTick = { t: t[0], bid: t[1], ask: t[2] }
      if (applied.length) this.emit(sym, { type: 'bars', bars: applied })
    }
    this.bump()
  }

  /** Forget symbols that are no longer displayed. */
  prune(keep: string[]): void {
    for (const s of [...this.data.keys()]) if (!keep.includes(s)) this.data.delete(s)
  }

  clear(): void {
    this.data.clear()
    this.status = 'closed'
    this.cutoffMs = 0
    this.bump()
  }
}
