import type { Direction, Horizon, PublicConfig, Timeframe } from './types'
import { TIMEFRAMES } from './types'

export type Layout = 1 | 2 | 4

export interface Settings {
  symbols: string[]          // one slot per possible chart (4)
  layout: Layout
  active: number             // index of the chart that Buy / Sell / the symbol picker act on
  tf: Timeframe
  delay: number              // seconds
  tz: string
  showBid: boolean
  showAsk: boolean
  showLabels: boolean        // never restored from storage: practice starts clean
  labelDir: Direction
  labelHorizon: Horizon
  mode: 'practice' | 'trade'    // BUY / SELL record practice calls, or place paper-trading orders
  lots: number
  slPips: number | null
  tpPips: number | null
}

const KEY = 'lab.settings.v1'
const HORIZONS: Horizon[] = ['pnl_30s', 'pnl_60s', 'pnl_120s', 'pnl_300s']

export function defaults(cfg: PublicConfig): Settings {
  const syms = cfg.symbols
  return {
    symbols: [0, 1, 2, 3].map((i) => syms[i % syms.length]),
    layout: 1, active: 0, tf: 'M1',
    delay: cfg.delay.default_seconds, tz: cfg.display_timezone,
    showBid: true, showAsk: false, showLabels: false, labelDir: 'buy', labelHorizon: 'pnl_60s',
    mode: 'practice', lots: 0.1, slPips: null, tpPips: null,
  }
}

/** Merge stored values over defaults, keeping only values the server would accept. */
export function sanitize(raw: unknown, cfg: PublicConfig): Settings {
  const d = defaults(cfg)
  if (!raw || typeof raw !== 'object') return d
  const r = raw as Record<string, unknown>
  const s = { ...d }
  if (Array.isArray(r.symbols)) {
    const stored: unknown[] = r.symbols
    s.symbols = d.symbols.map((def, i) => (typeof stored[i] === 'string' && cfg.symbols.includes(stored[i] as string) ? (stored[i] as string) : def))
  }
  if (r.layout === 1 || r.layout === 2 || r.layout === 4) s.layout = r.layout
  if (typeof r.active === 'number' && r.active >= 0 && r.active < s.layout) s.active = r.active
  if (TIMEFRAMES.includes(r.tf as Timeframe)) s.tf = r.tf as Timeframe
  if (typeof r.delay === 'number' && r.delay >= cfg.delay.min_seconds && r.delay <= cfg.delay.max_seconds) s.delay = Math.round(r.delay)
  if (typeof r.tz === 'string' && r.tz.length < 64) s.tz = r.tz
  if (typeof r.showBid === 'boolean') s.showBid = r.showBid
  if (typeof r.showAsk === 'boolean') s.showAsk = r.showAsk
  if (r.labelDir === 'buy' || r.labelDir === 'sell') s.labelDir = r.labelDir
  if (HORIZONS.includes(r.labelHorizon as Horizon)) s.labelHorizon = r.labelHorizon as Horizon
  if (r.mode === 'practice' || r.mode === 'trade') s.mode = r.mode
  if (typeof r.lots === 'number' && r.lots >= 0.01 && r.lots <= 50) s.lots = Number(r.lots.toFixed(2))
  for (const k of ['slPips', 'tpPips'] as const) {
    const v = r[k]
    if (v === null || (typeof v === 'number' && v > 0 && v <= 10000)) s[k] = v as number | null
  }
  s.showLabels = false
  return s
}

export function loadSettings(cfg: PublicConfig): Settings {
  try {
    const t = localStorage.getItem(KEY)
    return sanitize(t ? JSON.parse(t) : null, cfg)
  } catch { return defaults(cfg) }
}

export function saveSettings(s: Settings): void {
  try { localStorage.setItem(KEY, JSON.stringify({ ...s, showLabels: false })) } catch { /* private mode etc. */ }
}

/** The distinct symbols currently on screen, in slot order. */
export function visibleSymbols(s: Settings): string[] {
  return [...new Set(s.symbols.slice(0, s.layout))]
}

/** Random id that works on plain-http LAN pages (crypto.randomUUID needs a secure context). */
export function newSessionId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto
  if (c?.randomUUID) return c.randomUUID()
  return 'xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0
    return (ch === 'x' ? r : (r & 3) | 8).toString(16)
  })
}
