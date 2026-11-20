import { theme } from '../theme'
import { TF_SECONDS, type Call, type Direction, type Horizon, type Label, type Timeframe } from './types'

export interface Marker {
  time: number
  position: 'aboveBar' | 'belowBar' | 'inBar'
  color: string
  shape: 'arrowUp' | 'arrowDown' | 'circle' | 'square'
  text?: string
  size?: number
}

export const bucketOf = (tMs: number, tf: Timeframe): number => {
  const s = TF_SECONDS[tf]
  return Math.floor(tMs / 1000 / s) * s
}

/** Marker size from the size of the move: 0.4 (tiny) .. 1.3 (>= `fullAt` pips). Kept small so candles stay readable. */
export function sizeFor(pips: number, fullAt = 10): number {
  return 0.4 + 0.9 * Math.min(Math.abs(pips) / fullAt, 1)
}

/**
 * Labels overlay: one marker per candle for the chosen direction. On M1 that is one per
 * graded entry; on higher timeframes the entries inside a candle are averaged. Green =
 * positive net pips at `horizon`, red = negative, size = magnitude.
 */
export function labelMarkers(labels: Label[], tf: Timeframe, dir: Direction, horizon: Horizon): Marker[] {
  const acc = new Map<number, { sum: number; n: number }>()
  for (const l of labels) {
    const v = l[horizon]
    if (l.direction !== dir || l.status !== 'final' || v === null || v === undefined) continue
    const b = bucketOf(l.entry_t_ms, tf)
    const a = acc.get(b) ?? { sum: 0, n: 0 }
    a.sum += v; a.n += 1
    acc.set(b, a)
  }
  return [...acc.entries()].sort((x, y) => x[0] - y[0]).map(([time, a]) => {
    const avg = a.sum / a.n
    return {
      time,
      position: dir === 'buy' ? 'belowBar' : 'aboveBar',
      shape: dir === 'buy' ? 'arrowUp' : 'arrowDown',
      color: avg > 0 ? theme.labelWin : avg < 0 ? theme.labelLoss : theme.labelNeutral,
      size: sizeFor(avg),
    } satisfies Marker
  })
}

/** The user's own practice calls (always shown, independent of the labels toggle). */
export function callMarkers(calls: Call[], symbol: string, tf: Timeframe): Marker[] {
  return calls
    .filter((c) => c.symbol === symbol)
    .map((c) => ({
      time: bucketOf(c.t_ms, tf),
      position: c.direction === 'buy' ? 'belowBar' : 'aboveBar',
      shape: 'circle',
      color: c.direction === 'buy' ? theme.callBuy : theme.callSell,
      text: (c.direction === 'buy' ? 'B' : 'S') + (c.peeked ? '*' : ''),
      size: 1,
    } satisfies Marker))
}

/** Lightweight Charts needs markers sorted by time. */
export function mergeMarkers(...lists: Marker[][]): Marker[] {
  return lists.flat().sort((a, b) => a.time - b.time)
}
