// Back-office API client. Every call carries the login cookie; a 401 means "sign in again".
import type { BotInfo } from '../lib/bot'

export class AuthError extends Error {}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { credentials: 'same-origin', ...init, headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) } })
  if (res.status === 401) throw new AuthError('signed out')
  if (!res.ok) {
    let detail = ''
    try { const b = await res.json(); detail = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail) } catch { /* not json */ }
    throw new Error(detail || `${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<T>
}
const body = (method: string, b: unknown): RequestInit => ({ method, body: JSON.stringify(b) })

export const me = () => call<{ admin: boolean; username: string | null }>('/api/admin/me')
export const login = (username: string, password: string) => call<{ ok: true }>('/api/admin/login', body('POST', { username, password }))
export const logout = () => call<{ ok: true }>('/api/admin/logout', body('POST', {}))
export const resetPaperAccount = () => call('/api/trade/account/reset', body('POST', { confirm: true }))

export interface Summary {
  trades: number; net_usd: number; return_pct: number; win_pct: number | null; avg_net_pips: number | null; profit_factor: number | null
  max_drawdown_pct: number; avg_hold_s: number | null; exit_reasons: Record<string, number>; rejected: number; errors: number
  halted: boolean; halt_reason: string; forward_s?: number
}
export interface Stat { n: number; mean: number | null; z: number | null; robust?: boolean }
export interface Evaluation {
  summary: Summary
  selection_edge_ex_spread_pips?: Stat; direction_edge_pips?: Stat; selection_edge_pips?: Stat
  oracle?: { lift: number | null; bot_rate_pct: number; base_rate_pct: number; z: number | null }
  matching?: { deals: number; matched: number; note?: string }
}
export interface RunRow {
  id: number; kind: 'replay' | 'train'; status: 'running' | 'done' | 'failed'; title: string; forward_s: number
  created_ms: number; finished_ms: number | null; error: string | null; progress: string | null
  request: Record<string, unknown>; summary?: Record<string, number | string | null>
}
export interface TrainResult {
  model: string; verdict?: string
  analysis: { decisions: number; teacher_buy_pct: number; teacher_sell_pct: number; teacher_avg_net_pips_when_trading: number | null
              features: { feature: string; corr_trade: number; corr_direction: number }[] }
  fit: { n_train: number; n_validation: number; validation_auc_trade: number | null; validation_auc_direction: number | null; suggested_threshold: number }
  weights: { feature: string; w_buy: number; w_sell: number }[]
  test?: Record<string, { summary: Summary; direction_edge_pips?: Stat; selection_edge_ex_spread_pips?: Stat; forward_s: number }>
}
export interface ReplayResult {
  evaluation: Evaluation; equity: { t_ms: number; balance: number; equity: number; margin: number }[]; forward_s: number; tagged_peeked: boolean
  deals: { id: number; symbol: string; side: string; volume: number; open_ms: number; close_ms: number; net: number; net_pips: number; reason: string; peeked: number }[]
  symbols: string[]; params: Record<string, unknown>
}
export interface RunFull extends Omit<RunRow, 'summary'> { result: (ReplayResult & TrainResult) | null }
export interface ModelRow { name: string; trained_on?: string; suggested_threshold?: number; validation_auc_direction?: number | null; forward_s?: number; symbols?: string[] }
export interface LiveStatus {
  running: boolean; halted: boolean; terminal_delay_s: number
  bot: { name: string; params: Record<string, unknown>; symbols: string[]; forward_s?: number } | null
  stats?: { steps: number; opened: number; closed_by_bot: number; rejected: number; errors: number; halt_reason: string }
  log: { id: number; t_ms: number; kind: string; detail: string }[]
  account: { balance: number; equity: number }
  deals_by_tag: Record<'clean' | 'peeked', { trades: number; net_usd: number; avg_net_pips: number | null }>
}

export const labBots = () => call<{ bots: BotInfo[]; models: ModelRow[]; symbols: string[]; delay_s: number }>('/api/admin/lab/bots')
export const labRange = () => call<{ symbols: Record<string, { from_ms: number; to_ms: number }>; tz: string }>('/api/admin/lab/range')
export const labRuns = () => call<{ runs: RunRow[]; running: number | null }>('/api/admin/lab/runs?limit=60')
export const labRun = (id: number) => call<RunFull>(`/api/admin/lab/runs/${id}`)
export const labDeleteRun = (id: number) => call('/api/admin/lab/runs/' + id, { method: 'DELETE' })
export const labReplay = (b: unknown) => call<{ id: number }>('/api/admin/lab/replay', body('POST', b))
export const labTrain = (b: unknown) => call<{ id: number }>('/api/admin/lab/train', body('POST', b))
export const labModels = () => call<{ models: ModelRow[] }>('/api/admin/lab/models')
export const labDeleteModel = (n: string) => call('/api/admin/lab/models/' + encodeURIComponent(n), { method: 'DELETE' })
export const labLive = () => call<LiveStatus>('/api/admin/lab/live')
export const labStartLive = (b: unknown) => call<LiveStatus>('/api/admin/lab/live', body('POST', b))
export const labStopLive = (closePositions: boolean) => call('/api/bot/stop', body('POST', { close_positions: closePositions }))
