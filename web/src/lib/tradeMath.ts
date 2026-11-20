import type { Side } from './trade'

/** Price of an SL or TP that is `pips` away from the exit-side price (bid for buys, ask for sells). */
export function levelFromPips(side: Side, kind: 'sl' | 'tp', ref: number, pips: number, pip: number, digits: number): number {
  const favourable = (side === 'buy') === (kind === 'tp')          // TP of a buy and SL of a sell lie above
  const v = ref + (favourable ? 1 : -1) * pips * pip
  return Number(v.toFixed(digits))
}

/** Pips between a level and a reference price (always positive for a sensible level). */
export function pipsFrom(ref: number, level: number, pip: number): number {
  return Math.abs(level - ref) / pip
}

export function snapPrice(price: number, digits: number): number {
  return Number(price.toFixed(digits))
}

/** Next valid lot size: multiples of `step`, clamped to [min, max]; tolerant of float noise. */
export function stepLots(current: number, dir: 1 | -1, step = 0.01, min = 0.01, max = 50): number {
  const unit = step >= 0.1 ? 0.1 : step
  const inc = current >= 1 ? 0.5 : current >= 0.1 ? 0.1 : 0.01
  const next = Math.round((current + dir * Math.max(inc, unit)) / step) * step
  return Number(Math.min(max, Math.max(min, next)).toFixed(2))
}

export function parseLots(text: string, step = 0.01, min = 0.01, max = 50): number | null {
  const v = Number(text.trim().replace(',', '.'))
  if (!isFinite(v) || v <= 0) return null
  const snapped = Math.round(v / step) * step
  if (snapped < min - 1e-9 || snapped > max + 1e-9) return null
  return Number(snapped.toFixed(2))
}

export const fmtMoney = (v: number | null | undefined, signed = false): string =>
  v === null || v === undefined || !isFinite(v) ? '–'
    : `${signed && v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export const fmtPips = (v: number | null | undefined): string =>
  v === null || v === undefined || !isFinite(v) ? '–' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}`

export const fmtPrice = (v: number | null | undefined, digits: number): string =>
  v === null || v === undefined ? '–' : v.toFixed(digits)

/** Half of a position, as a valid lot size (null if it cannot be split). */
export function halfLots(volume: number, step = 0.01, min = 0.01): number | null {
  const h = Math.floor(volume / 2 / step) * step
  return h >= min - 1e-9 && volume - h >= min - 1e-9 ? Number(h.toFixed(2)) : null
}
