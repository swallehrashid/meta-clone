// Bot control API (server/api_bot.py) and the pure helpers behind the parameter form.
import { TradeError } from './trade'

export interface ParamSpec { type: 'float' | 'int' | 'bool' | 'choice' | 'str'; default: number | boolean | string; min: number | null; max: number | null; choices: string[]; help: string }
export interface BotInfo { name: string; description: string; interval_ms: number; needs_forward?: boolean; params: Record<string, ParamSpec> }
export interface BotEvent { id: number; t_ms: number; kind: string; detail: string }
export interface BotStatus {
  available: BotInfo[]
  running: boolean
  halted: boolean
  bot: { name: string; params: Record<string, number | boolean | string>; symbols: string[]; limits: Record<string, number> } | null
  stats?: { steps: number; decisions_with_actions: number; opened: number; closed_by_bot: number; rejected: number; errors: number; halt_reason: string; last_decision_ms: number | null }
  limits?: Record<string, number>
  log: BotEvent[]
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let body: { detail?: unknown } = {}
    try { body = await res.json() } catch { /* not json */ }
    throw new TradeError(typeof body.detail === 'string' ? body.detail : res.statusText, 'bot', res.status)
  }
  return res.json() as Promise<T>
}
const post = (body: object): RequestInit => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

export const getBot = () => call<BotStatus>('/api/bot?log=40')
export const startBot = (name: string, params: Record<string, unknown>, symbols: string[]) => call<BotStatus>('/api/bot/start', post({ name, params, symbols }))
export const stopBot = (closePositions: boolean) => call<BotStatus>('/api/bot/stop', post({ close_positions: closePositions }))

/** Text boxes hold strings; the server wants typed values. */
export type ParamText = Record<string, string>

export function defaultsOf(info: BotInfo): ParamText {
  return Object.fromEntries(Object.entries(info.params).map(([k, s]) => [k, String(s.default)]))
}

/** Parse the form into typed parameters, or return the first problem in words. */
export function parseParams(info: BotInfo, text: ParamText): { ok: true; params: Record<string, number | boolean | string> } | { ok: false; error: string } {
  const params: Record<string, number | boolean | string> = {}
  for (const [k, s] of Object.entries(info.params)) {
    const raw = (text[k] ?? '').trim()
    if (s.type === 'bool') { params[k] = raw === 'true'; continue }
    if (s.type === 'choice') {
      if (!s.choices.includes(raw)) return { ok: false, error: `${k}: pick one of ${s.choices.join(', ')}` }
      params[k] = raw; continue
    }
    if (s.type === 'str') { params[k] = raw; continue }
    const v = Number(raw)
    if (raw === '' || !isFinite(v)) return { ok: false, error: `${k}: enter a number` }
    if (s.type === 'int' && !Number.isInteger(v)) return { ok: false, error: `${k}: must be a whole number` }
    if (s.min !== null && v < s.min) return { ok: false, error: `${k}: at least ${s.min}` }
    if (s.max !== null && v > s.max) return { ok: false, error: `${k}: at most ${s.max}` }
    params[k] = v
  }
  return { ok: true, params }
}
