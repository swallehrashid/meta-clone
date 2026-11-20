import type { Call, Direction, Label, PublicConfig, Scorecard, Timeframe } from './types'

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = ''
    try { detail = (await res.json()).detail ?? '' } catch { /* not json */ }
    throw new Error(`${res.status} ${detail || res.statusText}`)
  }
  return res.json() as Promise<T>
}

export const getConfig = (): Promise<PublicConfig> => fetch('/api/config').then((r) => j<PublicConfig>(r))

export interface LabelQuery { symbol: string; tFrom: number; delay: number; limit?: number }
export const getLabels = (q: LabelQuery): Promise<{ cutoff_ms: number; labels: Label[] }> =>
  fetch(`/labels?symbol=${encodeURIComponent(q.symbol)}&t_from=${Math.floor(q.tFrom)}&delay=${q.delay}` +
        `&status=final&limit=${q.limit ?? 50000}`).then((r) => j(r))

export const postCall = (body: { symbol: string; direction: Direction; delay: number; peeked: boolean; session_id: string }): Promise<Call> =>
  fetch('/calls', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
    .then((r) => j<Call>(r))

export const getCalls = (limit = 100): Promise<{ calls: Call[] }> =>
  fetch(`/calls?limit=${limit}`).then((r) => j(r))

export const getScorecard = (): Promise<Scorecard> => fetch('/calls/scorecard').then((r) => j<Scorecard>(r))

// ---- terminal settings (back office) -------------------------------------------------------------
export interface TerminalSettings {
  delay_s: number; tf: Timeframe; volume: number; show_ask_line: boolean; tz: string; symbols: string[]
  account_name: string; server_name: string; login: number
  broker_symbols: Record<string, string>; descriptions: Record<string, string>; timeframes: Timeframe[]
  delay: { min: number; max: number }; all_symbols?: string[]
}
export const getTerminal = (): Promise<TerminalSettings> => fetch('/api/terminal').then((r) => j<TerminalSettings>(r))
export const getAdminTerminal = (): Promise<TerminalSettings> => fetch('/api/admin/terminal').then((r) => j<TerminalSettings>(r))
export const putAdminTerminal = (patch: Partial<TerminalSettings>): Promise<TerminalSettings> =>
  fetch('/api/admin/terminal', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(patch) }).then((r) => j<TerminalSettings>(r))

export interface Quote {
  symbol: string; bid: number; ask: number; t_ms: number; digits: number; pip: number
  spread_pts: number; change_pts: number; change_pct: number; low: number; high: number
}
export const getQuotes = (): Promise<{ delay_s: number; cutoff_ms: number; quotes: Quote[] }> => fetch('/api/terminal/quotes').then((r) => j(r))
