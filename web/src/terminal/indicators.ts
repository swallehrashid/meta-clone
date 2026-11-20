// Indicators the chart can draw, with MT5's names, groups, default parameters and formulas.
import type { Bar } from '../lib/types'
import type { IndicatorConf } from './prefs'

export type V = (number | null)[]
export interface Output { kind: 'line' | 'hist' | 'dots'; values: V; color: string; colors?: (string | null)[]; width?: number }
export interface ParamDef { key: string; label: string; def: number | string; min?: number; max?: number; step?: number; options?: string[] }
export interface IndicatorDef {
  type: string; name: string; group: 'Trend' | 'Oscillators' | 'Volumes' | 'Bill Williams'; sub: boolean
  params: ParamDef[]; colors: string[]; levels?: number[]
  calc: (b: Bar[], p: Record<string, number | string>, colors: string[]) => Output[]
}

const METHODS = ['Simple', 'Exponential', 'Smoothed', 'Linear Weighted']
const APPLY = ['Close', 'Open', 'High', 'Low', 'Median Price (HL/2)', 'Typical Price (HLC/3)', 'Weighted Close (HLCC/4)']

export function price(b: Bar[], apply: string): number[] {
  switch (apply) {
    case 'Open': return b.map((x) => x.open)
    case 'High': return b.map((x) => x.high)
    case 'Low': return b.map((x) => x.low)
    case 'Median Price (HL/2)': return b.map((x) => (x.high + x.low) / 2)
    case 'Typical Price (HLC/3)': return b.map((x) => (x.high + x.low + x.close) / 3)
    case 'Weighted Close (HLCC/4)': return b.map((x) => (x.high + x.low + 2 * x.close) / 4)
    default: return b.map((x) => x.close)
  }
}

export function sma(v: (number | null)[], n: number): V {
  const out: V = new Array(v.length).fill(null)
  let s = 0, c = 0
  for (let i = 0; i < v.length; i++) {
    const x = v[i]
    if (x === null) { s = 0; c = 0; continue }
    s += x; c++
    if (c > n) { s -= v[i - n] as number; c = n }
    if (c === n) out[i] = s / n
  }
  return out
}
export function ema(v: (number | null)[], n: number): V {
  const out: V = new Array(v.length).fill(null), k = 2 / (n + 1)
  let prev: number | null = null, seed: number[] = []
  for (let i = 0; i < v.length; i++) {
    const x = v[i]
    if (x === null) continue
    if (prev === null) { seed.push(x); if (seed.length === n) { prev = seed.reduce((a, b) => a + b, 0) / n; out[i] = prev; seed = [] } continue }
    prev = x * k + prev * (1 - k); out[i] = prev
  }
  return out
}
export function smma(v: (number | null)[], n: number): V {
  const out: V = new Array(v.length).fill(null)
  let prev: number | null = null, seed: number[] = []
  for (let i = 0; i < v.length; i++) {
    const x = v[i]
    if (x === null) continue
    if (prev === null) { seed.push(x); if (seed.length === n) { prev = seed.reduce((a, b) => a + b, 0) / n; out[i] = prev } continue }
    prev = (prev * (n - 1) + x) / n; out[i] = prev
  }
  return out
}
export function lwma(v: (number | null)[], n: number): V {
  const out: V = new Array(v.length).fill(null), den = (n * (n + 1)) / 2
  for (let i = n - 1; i < v.length; i++) {
    let s = 0, ok = true
    for (let j = 0; j < n; j++) { const x = v[i - j]; if (x === null) { ok = false; break } s += x * (n - j) }
    if (ok) out[i] = s / den
  }
  return out
}
export function ma(v: (number | null)[], n: number, method: string): V {
  return method === 'Exponential' ? ema(v, n) : method === 'Smoothed' ? smma(v, n) : method === 'Linear Weighted' ? lwma(v, n) : sma(v, n)
}
function stdev(v: number[], n: number, mean: V): V {
  return v.map((_, i) => {
    const m = mean[i]
    if (m === null || i < n - 1) return null
    let s = 0
    for (let j = 0; j < n; j++) s += (v[i - j] - m) ** 2
    return Math.sqrt(s / n)
  })
}
const shift = (v: V, s: number): V => (s ? v.map((_, i) => (i - s >= 0 && i - s < v.length ? v[i - s] : null)) : v)
const num = (p: Record<string, number | string>, k: string) => Number(p[k])

export function rsi(c: number[], n: number): V {
  const out: V = new Array(c.length).fill(null)
  let up = 0, dn = 0
  for (let i = 1; i < c.length; i++) {
    const d = c[i] - c[i - 1], u = Math.max(d, 0), w = Math.max(-d, 0)
    if (i <= n) { up += u; dn += w; if (i === n) { up /= n; dn /= n; out[i] = dn === 0 ? 100 : 100 - 100 / (1 + up / dn) } continue }
    up = (up * (n - 1) + u) / n; dn = (dn * (n - 1) + w) / n
    out[i] = dn === 0 ? 100 : 100 - 100 / (1 + up / dn)
  }
  return out
}
export function atr(b: Bar[], n: number): V {
  const tr = b.map((x, i) => (i === 0 ? x.high - x.low : Math.max(x.high, b[i - 1].close) - Math.min(x.low, b[i - 1].close)))
  return sma(tr, n)
}
export function stochastic(b: Bar[], k: number, d: number, slow: number): [V, V] {
  const raw: V = b.map((_, i) => {
    if (i < k - 1) return null
    let hi = -Infinity, lo = Infinity
    for (let j = 0; j < k; j++) { hi = Math.max(hi, b[i - j].high); lo = Math.min(lo, b[i - j].low) }
    return hi === lo ? 50 : ((b[i].close - lo) / (hi - lo)) * 100
  })
  const K = sma(raw, slow)
  return [K, sma(K, d)]
}
export function psar(b: Bar[], step: number, max: number): V {
  const out: V = new Array(b.length).fill(null)
  if (b.length < 3) return out
  let up = b[1].close >= b[0].close, af = step, ep = up ? b[0].high : b[0].low, sar = up ? b[0].low : b[0].high
  for (let i = 1; i < b.length; i++) {
    sar = sar + af * (ep - sar)
    if (up) {
      sar = Math.min(sar, b[i - 1].low, i > 1 ? b[i - 2].low : b[i - 1].low)
      if (b[i].low < sar) { up = false; sar = ep; ep = b[i].low; af = step }
      else if (b[i].high > ep) { ep = b[i].high; af = Math.min(af + step, max) }
    } else {
      sar = Math.max(sar, b[i - 1].high, i > 1 ? b[i - 2].high : b[i - 1].high)
      if (b[i].high > sar) { up = true; sar = ep; ep = b[i].high; af = step }
      else if (b[i].low < ep) { ep = b[i].low; af = Math.min(af + step, max) }
    }
    out[i] = sar
  }
  return out
}

const period = (def: number, label = 'Period'): ParamDef => ({ key: 'period', label, def, min: 1, max: 500, step: 1 })
const method: ParamDef = { key: 'method', label: 'Method', def: 'Simple', options: METHODS }
const apply: ParamDef = { key: 'apply', label: 'Apply to', def: 'Close', options: APPLY }
const shiftP: ParamDef = { key: 'shift', label: 'Shift', def: 0, min: -100, max: 100, step: 1 }

export const INDICATORS: IndicatorDef[] = [
  { type: 'ma', name: 'Moving Average', group: 'Trend', sub: false, params: [period(14), shiftP, method, apply], colors: ['#f5b800'],
    calc: (b, p, c) => [{ kind: 'line', values: shift(ma(price(b, String(p.apply)), num(p, 'period'), String(p.method)), num(p, 'shift')), color: c[0] }] },
  { type: 'bands', name: 'Bollinger Bands', group: 'Trend', sub: false,
    params: [period(20), { key: 'dev', label: 'Deviations', def: 2, min: 0.1, max: 10, step: 0.1 }, shiftP, apply], colors: ['#2fbfbf'],
    calc: (b, p, c) => {
      const v = price(b, String(p.apply)), n = num(p, 'period'), m = sma(v, n), sd = stdev(v, n, m), k = num(p, 'dev'), s = num(p, 'shift')
      return [{ kind: 'line', values: shift(m, s), color: c[0] },
              { kind: 'line', values: shift(m.map((x, i) => (x === null || sd[i] === null ? null : x + k * (sd[i] as number))), s), color: c[0] },
              { kind: 'line', values: shift(m.map((x, i) => (x === null || sd[i] === null ? null : x - k * (sd[i] as number))), s), color: c[0] }]
    } },
  { type: 'env', name: 'Envelopes', group: 'Trend', sub: false,
    params: [period(14), { key: 'dev', label: 'Deviation (%)', def: 0.1, min: 0.01, max: 10, step: 0.01 }, shiftP, method, apply], colors: ['#3d8cf4', '#ef4f4c'],
    calc: (b, p, c) => {
      const m = shift(ma(price(b, String(p.apply)), num(p, 'period'), String(p.method)), num(p, 'shift')), d = num(p, 'dev') / 100
      return [{ kind: 'line', values: m.map((x) => (x === null ? null : x * (1 + d))), color: c[0] }, { kind: 'line', values: m.map((x) => (x === null ? null : x * (1 - d))), color: c[1] ?? c[0] }]
    } },
  { type: 'sar', name: 'Parabolic SAR', group: 'Trend', sub: false,
    params: [{ key: 'step', label: 'Step', def: 0.02, min: 0.001, max: 1, step: 0.001 }, { key: 'max', label: 'Maximum', def: 0.2, min: 0.01, max: 1, step: 0.01 }], colors: ['#36b24a'],
    calc: (b, p, c) => [{ kind: 'dots', values: psar(b, num(p, 'step'), num(p, 'max')), color: c[0] }] },
  { type: 'stddev', name: 'Standard Deviation', group: 'Trend', sub: true, params: [period(20), method, apply], colors: ['#3d8cf4'],
    calc: (b, p, c) => { const v = price(b, String(p.apply)), n = num(p, 'period'); return [{ kind: 'line', values: stdev(v, n, ma(v, n, String(p.method))), color: c[0] }] } },
  { type: 'rsi', name: 'Relative Strength Index', group: 'Oscillators', sub: true, params: [period(14), apply], colors: ['#3d8cf4'], levels: [30, 70],
    calc: (b, p, c) => [{ kind: 'line', values: rsi(price(b, String(p.apply)), num(p, 'period')), color: c[0] }] },
  { type: 'macd', name: 'MACD', group: 'Oscillators', sub: true,
    params: [{ key: 'fast', label: 'Fast EMA', def: 12, min: 1, max: 200 }, { key: 'slow', label: 'Slow EMA', def: 26, min: 1, max: 400 }, { key: 'signal', label: 'MACD SMA', def: 9, min: 1, max: 200 }, apply],
    colors: ['#9aa0a6', '#ef4f4c'],
    calc: (b, p, c) => {
      const v = price(b, String(p.apply)), f = ema(v, num(p, 'fast')), s = ema(v, num(p, 'slow'))
      const m: V = f.map((x, i) => (x === null || s[i] === null ? null : x - (s[i] as number)))
      return [{ kind: 'hist', values: m, color: c[0] }, { kind: 'line', values: sma(m, num(p, 'signal')), color: c[1] ?? '#ef4f4c' }]
    } },
  { type: 'stoch', name: 'Stochastic Oscillator', group: 'Oscillators', sub: true,
    params: [{ key: 'k', label: '%K period', def: 5, min: 1, max: 200 }, { key: 'd', label: '%D period', def: 3, min: 1, max: 200 }, { key: 'slowing', label: 'Slowing', def: 3, min: 1, max: 200 }],
    colors: ['#36b24a', '#ef4f4c'], levels: [20, 80],
    calc: (b, p, c) => { const [k, d] = stochastic(b, num(p, 'k'), num(p, 'd'), num(p, 'slowing')); return [{ kind: 'line', values: k, color: c[0] }, { kind: 'line', values: d, color: c[1] ?? '#ef4f4c' }] } },
  { type: 'atr', name: 'Average True Range', group: 'Oscillators', sub: true, params: [period(14)], colors: ['#3d8cf4'],
    calc: (b, p, c) => [{ kind: 'line', values: atr(b, num(p, 'period')), color: c[0] }] },
  { type: 'cci', name: 'Commodity Channel Index', group: 'Oscillators', sub: true, params: [period(14), { ...apply, def: 'Typical Price (HLC/3)' }], colors: ['#3fbfbf'], levels: [-100, 100],
    calc: (b, p, c) => {
      const v = price(b, String(p.apply)), n = num(p, 'period'), m = sma(v, n)
      return [{ kind: 'line', color: c[0], values: v.map((x, i) => {
        const mm = m[i]; if (mm === null) return null
        let md = 0; for (let j = 0; j < n; j++) md += Math.abs(v[i - j] - mm)
        md /= n; return md === 0 ? 0 : (x - mm) / (0.015 * md)
      }) }]
    } },
  { type: 'momentum', name: 'Momentum', group: 'Oscillators', sub: true, params: [period(14), apply], colors: ['#3d8cf4'], levels: [100],
    calc: (b, p, c) => { const v = price(b, String(p.apply)), n = num(p, 'period'); return [{ kind: 'line', color: c[0], values: v.map((x, i) => (i < n || !v[i - n] ? null : (x / v[i - n]) * 100)) }] } },
  { type: 'wpr', name: "Williams' Percent Range", group: 'Oscillators', sub: true, params: [period(14)], colors: ['#3fbfbf'], levels: [-80, -20],
    calc: (b, p, c) => { const n = num(p, 'period'); return [{ kind: 'line', color: c[0], values: b.map((x, i) => {
      if (i < n - 1) return null
      let hi = -Infinity, lo = Infinity
      for (let j = 0; j < n; j++) { hi = Math.max(hi, b[i - j].high); lo = Math.min(lo, b[i - j].low) }
      return hi === lo ? -50 : ((hi - x.close) / (hi - lo)) * -100
    }) }] } },
  { type: 'demarker', name: 'DeMarker', group: 'Oscillators', sub: true, params: [period(14)], colors: ['#3d8cf4'], levels: [0.3, 0.7],
    calc: (b, p, c) => {
      const n = num(p, 'period')
      const mx = b.map((x, i) => (i && x.high > b[i - 1].high ? x.high - b[i - 1].high : 0)), mn = b.map((x, i) => (i && x.low < b[i - 1].low ? b[i - 1].low - x.low : 0))
      const a = sma(mx, n), d = sma(mn, n)
      return [{ kind: 'line', color: c[0], values: a.map((x, i) => (x === null || d[i] === null ? null : x + (d[i] as number) === 0 ? 0.5 : x / (x + (d[i] as number)))) }]
    } },
  { type: 'volumes', name: 'Volumes', group: 'Volumes', sub: true, params: [{ key: 'vol', label: 'Volume', def: 'Tick', options: ['Tick'] }], colors: ['#36b24a', '#ef4f4c'],
    calc: (b, _p, c) => [{ kind: 'hist', values: b.map((x) => x.ticks ?? 0), color: c[0], colors: b.map((x, i) => (i && (x.ticks ?? 0) < (b[i - 1].ticks ?? 0) ? c[1] ?? '#ef4f4c' : c[0])) }] },
  { type: 'ao', name: 'Awesome Oscillator', group: 'Bill Williams', sub: true, params: [], colors: ['#36b24a', '#ef4f4c'],
    calc: (b, _p, c) => {
      const med = price(b, 'Median Price (HL/2)'), f = sma(med, 5), s = sma(med, 34)
      const v: V = f.map((x, i) => (x === null || s[i] === null ? null : x - (s[i] as number)))
      return [{ kind: 'hist', values: v, color: c[0], colors: v.map((x, i) => (x === null ? null : i && v[i - 1] !== null && x < (v[i - 1] as number) ? c[1] ?? '#ef4f4c' : c[0])) }]
    } },
]

export const byType = (t: string) => INDICATORS.find((d) => d.type === t)

export function defaultsFor(def: IndicatorDef): Record<string, number | string> {
  return Object.fromEntries(def.params.map((p) => [p.key, p.def]))
}

/** "Moving Average (14, Simple)" style label used on the Indicators page */
export function label(c: IndicatorConf): string {
  const d = byType(c.type)
  if (!d) return c.type
  const vals = d.params.filter((p) => p.key !== 'apply').map((p) => String(c.params[p.key] ?? p.def))
  return vals.length ? `${d.name} (${vals.join(', ')})` : d.name
}
