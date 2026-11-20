import { describe, expect, it } from 'vitest'
import { bucketOf, callMarkers, labelMarkers, mergeMarkers, sizeFor } from './markers'
import { theme } from '../theme'
import type { Call, Label } from './types'

const T = Date.parse('2025-03-04T14:00:00Z')
const lab = (min: number, dir: 'buy' | 'sell', pnl: number | null, status: Label['status'] = 'final'): Label => ({
  symbol: 'EURUSD', entry_t_ms: T + min * 60_000, direction: dir, status, entry_price: 1, spread_pips: 1,
  pnl_30s: pnl, pnl_60s: pnl, pnl_120s: pnl, pnl_300s: pnl, mfe_pips: 0, mae_pips: 0,
})

describe('label markers', () => {
  it('colours by sign and sizes by magnitude, for the chosen direction only', () => {
    const m = labelMarkers([lab(0, 'buy', 6), lab(1, 'buy', -2), lab(2, 'buy', 0), lab(0, 'sell', -9)], 'M1', 'buy', 'pnl_60s')
    expect(m.map((x) => x.color)).toEqual([theme.labelWin, theme.labelLoss, theme.labelNeutral])
    expect(m[0].size!).toBeGreaterThan(m[1].size!)
    expect(m.every((x) => x.shape === 'arrowUp' && x.position === 'belowBar')).toBe(true)
    expect(labelMarkers([lab(0, 'sell', -9)], 'M1', 'sell', 'pnl_60s')[0]).toMatchObject({ shape: 'arrowDown', position: 'aboveBar', color: theme.labelLoss })
  })
  it('skips pending and unknown results', () => {
    expect(labelMarkers([lab(0, 'buy', null), lab(1, 'buy', 5, 'pending')], 'M1', 'buy', 'pnl_60s')).toEqual([])
  })
  it('averages the entries inside a higher-timeframe candle and sorts by time', () => {
    const ls = [lab(7, 'buy', 4), lab(6, 'buy', -6), lab(1, 'buy', 3)]       // 14:06,14:07 share the M5 candle 14:05
    const m = labelMarkers(ls, 'M5', 'buy', 'pnl_60s')
    expect(m.map((x) => x.time)).toEqual([T / 1000, T / 1000 + 300])
    expect(m[1].color).toBe(theme.labelLoss)                                  // (4 + -6)/2 = -1
  })
  it('size is monotonic and clamped', () => {
    expect(sizeFor(0)).toBeCloseTo(0.4)
    expect(sizeFor(1000)).toBeCloseTo(1.3)
    expect(sizeFor(5)).toBeLessThan(sizeFor(10))
    expect(sizeFor(1000)).toBe(sizeFor(10))
  })
})

describe('call markers + merge', () => {
  const call = (id: number, sym: string, min: number, dir: 'buy' | 'sell', peeked = 0): Call => ({
    id, symbol: sym, t_ms: T + min * 60_000 + 20_000, direction: dir, note: '', peeked, status: 'pending',
    entry_price: null, spread_pips: null, pnl_30s: null, pnl_60s: null, pnl_120s: null, pnl_300s: null, mfe_pips: null, mae_pips: null,
  })
  it('marks the candle containing the call, flags peeked calls with *', () => {
    const m = callMarkers([call(1, 'EURUSD', 3, 'buy'), call(2, 'EURUSD', 4, 'sell', 1), call(3, 'GBPUSD', 3, 'buy')], 'EURUSD', 'M1')
    expect(m.map((x) => [x.time, x.text])).toEqual([[T / 1000 + 180, 'B'], [T / 1000 + 240, 'S*']])
  })
  it('merges into one time-sorted list', () => {
    const a = labelMarkers([lab(5, 'buy', 1)], 'M1', 'buy', 'pnl_60s')
    const b = callMarkers([call(1, 'EURUSD', 2, 'buy')], 'EURUSD', 'M1')
    expect(mergeMarkers(a, b).map((x) => x.time)).toEqual([T / 1000 + 120, T / 1000 + 300])
  })
  it('bucketOf floors to the timeframe', () => {
    expect(bucketOf(T + 7 * 60_000 + 59_999, 'M5')).toBe(T / 1000 + 300)
    expect(bucketOf(T + 59 * 60_000, 'H1')).toBe(T / 1000)
  })
})
