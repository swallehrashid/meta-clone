// Paper-trading API (server/api_account.py). Every call carries the delay of the chart it was made on.
export type Side = 'buy' | 'sell'
export type OrderType = 'market' | 'limit' | 'stop' | 'stop_limit'

export interface Account {
  currency: string; leverage: number; balance: number; equity: number; margin: number; free_margin: number
  margin_level: number | null; unrealized: number; realized: number; deals: number; positions: number; orders: number
  t_engine_ms: number; delay_s: number; initial_balance: number; peak_equity: number; drawdown_pct: number
}
export interface TPosition {
  id: number; symbol: string; side: Side; volume: number; open_price: number; open_ms: number
  sl: number | null; tp: number | null; source: string; peeked: number
  price: number | null; pips: number | null; profit: number | null; margin: number; commission: number
}
export interface TOrder {
  id: number; symbol: string; side: Side; type: OrderType; volume: number; price: number | null
  sl: number | null; tp: number | null; status: string; placed_ms: number; kind: string; peeked: number
  stoplimit?: number | null; expire_ms?: number | null; done_ms?: number | null; comment?: string; reason?: string; position_id?: number | null
}
export interface Deal {
  id: number; position_id: number; symbol: string; side: Side; volume: number; open_price: number; open_ms: number
  close_price: number; close_ms: number; sl: number | null; tp: number | null; pips: number; gross: number; commission: number; net: number; net_pips: number
  reason: string; source: string; peeked: number; duration_s: number
}
export interface TradeState { account: Account; positions: TPosition[]; orders: TOrder[] }
export interface EquityPoint { t_ms: number; balance: number; equity: number; margin: number }

export class TradeError extends Error {
  constructor(message: string, public code: string, public status: number) { super(message) }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let body: { detail?: unknown; code?: string } = {}
    try { body = await res.json() } catch { /* not json */ }
    const detail = typeof body.detail === 'string' ? body.detail : res.statusText
    throw new TradeError(detail, body.code ?? 'error', res.status)
  }
  return res.json() as Promise<T>
}
const post = (body: object, method = 'POST'): RequestInit =>
  ({ method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

export const getTradeState = (delay: number) => call<TradeState>(`/api/trade/state?delay=${delay}`)
export const getHistory = (limit = 200) => call<{ deals: Deal[] }>(`/api/trade/history?limit=${limit}`)
export const getEquity = () => call<{ points: EquityPoint[] }>('/api/trade/equity')
export const getOrderHistory = (limit = 1000) => call<{ orders: TOrder[] }>(`/api/trade/orders_history?limit=${limit}`)

export interface PlaceArgs {
  symbol: string; side: Side; type: OrderType; volume: number; price?: number | null
  sl?: number | null; tp?: number | null; delay: number; peeked: boolean; session_id: string
  stoplimit?: number | null; expiration?: 'gtc' | 'today' | 'specified'; expire_ms?: number | null; comment?: string
}
export const placeOrder = (a: PlaceArgs) => call<TradeState & { id: number; status: string; position_id: number | null; price: number | null }>(
  '/api/trade/orders', post({ ...a, price: a.price ?? null, sl: a.sl ?? null, tp: a.tp ?? null }))
export const closePosition = (id: number, delay: number, volume?: number) =>
  call<TradeState>(`/api/trade/positions/${id}/close`, post({ delay, ...(volume ? { volume } : {}) }))
export const closeAll = (delay: number, symbol?: string) =>
  call<TradeState>('/api/trade/positions/close_all', post({ delay, ...(symbol ? { symbol } : {}) }))
/** sl / tp: a number sets it, null clears it, omitted keeps it. */
export const modifyPosition = (id: number, delay: number, change: { sl?: number | null; tp?: number | null }) =>
  call<TradeState>(`/api/trade/positions/${id}`, post({ delay, ...change }, 'PATCH'))
export const modifyOrder = (id: number, delay: number, change: { price?: number; sl?: number | null; tp?: number | null; stoplimit?: number | null }) =>
  call<TradeState>(`/api/trade/orders/${id}`, post({ delay, ...change }, 'PATCH'))
export const cancelOrder = (id: number, delay: number) =>
  call<TradeState>(`/api/trade/orders/${id}?delay=${delay}`, { method: 'DELETE' })
export const resetAccount = (balance?: number) =>
  call<TradeState>('/api/trade/account/reset', post({ confirm: true, ...(balance ? { balance } : {}) }))
