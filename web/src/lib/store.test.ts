import { describe, expect, it } from 'vitest'
import { MAX_BARS, MarketStore, type SymEvent } from './store'
import type { BackfillMsg, Bar, UpdateMsg } from './types'

const bar = (time: number, c: number): Bar => ({ time, open: c, high: c, low: c, close: c })
const backfill = (sym: string, candles: Bar[], cutoff = 1_000_000, delay = 300): BackfillMsg => ({
  type: 'backfill', reset: true, symbol: sym, tf: 'M1', delay, cutoff_ms: cutoff, digits: 5, pip: 1e-4,
  candles, last_tick: [cutoff - 5, 1.1, 1.1002],
})
const update = (sym: string, candles: Bar[], ticks: [number, number, number][], cutoff: number): UpdateMsg => ({
  type: 'update', cutoff_ms: cutoff, symbols: { [sym]: { ticks, candles } },
})

describe('MarketStore', () => {
  it('backfill sets the series and emits a reset; updates merge and emit the changed bars', () => {
    const s = new MarketStore(); const ev: SymEvent[] = []
    s.onSymbol('EURUSD', (e) => ev.push(e))
    s.handle(backfill('EURUSD', [bar(60, 1), bar(120, 2)]))
    s.handle(update('EURUSD', [bar(120, 3), bar(180, 4)], [[170_000, 1.2, 1.2002]], 1_000_250))
    const d = s.data.get('EURUSD')!
    expect(d.bars.map((b) => b.close)).toEqual([1, 3, 4])
    expect(d.lastTick).toEqual({ t: 170_000, bid: 1.2, ask: 1.2002 })
    expect(ev.map((e) => e.type)).toEqual(['reset', 'bars'])
    expect(s.cutoffMs).toBe(1_000_250)
  })
  it('a new backfill (delay change) may move the cutoff BACK, updates never do', () => {
    const s = new MarketStore()
    s.handle(backfill('EURUSD', [bar(60, 1)], 5_000_000, 60))
    s.handle({ type: 'heartbeat', cutoff_ms: 4_000_000 })
    expect(s.cutoffMs).toBe(5_000_000)
    s.handle(backfill('EURUSD', [bar(60, 1)], 4_100_000, 900))
    expect(s.cutoffMs).toBe(4_100_000)
    expect(s.delay).toBe(900)
  })
  it('ignores updates for symbols it has no backfill for, and prunes unused ones', () => {
    const s = new MarketStore()
    s.handle(update('GBPUSD', [bar(60, 1)], [], 1))
    expect(s.data.size).toBe(0)
    s.handle(backfill('EURUSD', [bar(60, 1)])); s.handle(backfill('GBPUSD', [bar(60, 1)]))
    s.prune(['EURUSD'])
    expect([...s.data.keys()]).toEqual(['EURUSD'])
  })
  it('memory stays bounded on a long session', () => {
    const s = new MarketStore()
    s.handle(backfill('EURUSD', [bar(0, 1)]))
    for (let i = 1; i <= MAX_BARS + 500; i++) s.handle(update('EURUSD', [bar(i * 60, i)], [], i))
    expect(s.data.get('EURUSD')!.bars.length).toBe(MAX_BARS)
    expect(s.data.get('EURUSD')!.bars.at(-1)!.close).toBe(MAX_BARS + 500)
  })
  it('stores the server error text and status', () => {
    const s = new MarketStore()
    s.handle({ type: 'error', message: 'delay must be between 1 and 86400 seconds' })
    expect(s.error).toMatch(/delay/)
    s.setStatus('open'); expect(s.status).toBe('open'); expect(s.error).toBe('')
  })
})
