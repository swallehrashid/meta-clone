export type Timeframe = 'M1' | 'M5' | 'M15' | 'M30' | 'H1' | 'H4' | 'D1'
export const TIMEFRAMES: Timeframe[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1']
export const TF_SECONDS: Record<Timeframe, number> = { M1: 60, M5: 300, M15: 900, M30: 1800, H1: 3600, H4: 14400, D1: 86400 }

/** Candle in Lightweight Charts form: `time` is UTC seconds of the bucket open. */
export interface Bar {
  time: number
  open: number
  high: number
  low: number
  close: number
  ticks?: number
  avg_spread?: number
}

export type Tick = [tMs: number, bid: number, ask: number]

export interface BackfillMsg {
  type: 'backfill'
  reset: boolean
  symbol: string
  tf: Timeframe
  delay: number
  cutoff_ms: number
  digits: number
  pip: number
  candles: Bar[]
  last_tick: Tick | null
}
export interface UpdateMsg {
  type: 'update'
  cutoff_ms: number
  symbols: Record<string, { ticks: Tick[]; candles: Bar[] }>
}
export interface HeartbeatMsg { type: 'heartbeat'; cutoff_ms: number }
export interface ErrorMsg { type: 'error'; message: string }
export type ServerMsg = BackfillMsg | UpdateMsg | HeartbeatMsg | ErrorMsg

export type Direction = 'buy' | 'sell'
export type Horizon = 'pnl_30s' | 'pnl_60s' | 'pnl_120s' | 'pnl_300s'
export const HORIZONS: Horizon[] = ['pnl_30s', 'pnl_60s', 'pnl_120s', 'pnl_300s']
export const HORIZON_LABEL: Record<Horizon, string> = {
  pnl_30s: '30s', pnl_60s: '60s', pnl_120s: '2m', pnl_300s: '5m',
}

export interface Label {
  symbol: string
  entry_t_ms: number
  direction: Direction
  status: 'pending' | 'final'
  entry_price: number | null
  spread_pips: number | null
  pnl_30s: number | null
  pnl_60s: number | null
  pnl_120s: number | null
  pnl_300s: number | null
  mfe_pips: number | null
  mae_pips: number | null
}

export interface Call {
  id: number
  symbol: string
  t_ms: number
  direction: Direction
  note: string
  peeked: number
  status: 'pending' | 'final' | 'ungradable'
  entry_price: number | null
  spread_pips: number | null
  pnl_30s: number | null
  pnl_60s: number | null
  pnl_120s: number | null
  pnl_300s: number | null
  mfe_pips: number | null
  mae_pips: number | null
}

export interface ScoreGroup {
  calls: number
  graded: number
  pending: number
  ungradable: number
  avg_pips: Record<Horizon, number | null>
  win_rate: Record<Horizon, number | null>
  avg_mfe_pips: number | null
  avg_mae_pips: number | null
  avg_spread_pips: number | null
}
export interface Scorecard { all: ScoreGroup; clean: ScoreGroup; peeked: ScoreGroup }

export interface PublicConfig {
  symbols: string[]
  timeframes: Timeframe[]
  delay: { default_seconds: number; presets_seconds: number[]; min_seconds: number; max_seconds: number }
  display_timezone: string
  price_side: 'bid' | 'ask'
}
