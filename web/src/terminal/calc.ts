// Trading arithmetic the panels show (margin, profit in money / points), mirroring the server engine's conventions:
// contract 100 000, margin = lots x contract x base-currency-in-USD / leverage, account in USD.
export const CONTRACT = 100_000

type Q = { bid: number; ask: number }

function mid(q?: Q): number | null { return q ? (q.bid + q.ask) / 2 : null }

/** USD value of one unit of the BASE currency of `symbol` (EURJPY -> EURUSD mid). */
export function usdPerBase(symbol: string, quotes: Map<string, Q>): number | null {
  const s = symbol.slice(0, 6), base = s.slice(0, 3), quote = s.slice(3, 6)
  if (base === 'USD') return 1
  if (quote === 'USD') return mid(quotes.get(symbol)) ?? null
  for (const [k, q] of quotes) {
    if (k.startsWith(base + 'USD')) return mid(q)
    if (k.startsWith('USD' + base)) { const m = mid(q); return m ? 1 / m : null }
  }
  return null
}

/** USD value of one unit of the QUOTE currency of `symbol` (EURJPY -> 1 / USDJPY, or EURUSD/EURJPY). */
export function usdPerQuote(symbol: string, quotes: Map<string, Q>): number | null {
  const s = symbol.slice(0, 6), base = s.slice(0, 3), quote = s.slice(3, 6)
  if (quote === 'USD') return 1
  if (base === 'USD') { const m = mid(quotes.get(symbol)); return m ? 1 / m : null }
  const b = usdPerBase(symbol, quotes), m = mid(quotes.get(symbol))
  return b && m ? b / m : null
}

export function marginUsd(symbol: string, lots: number, leverage: number, quotes: Map<string, Q>): number | null {
  const b = usdPerBase(symbol, quotes)
  return b === null ? null : (lots * CONTRACT * b) / (leverage || 100)
}

/** Profit in USD of `lots` moving from `open` to `close` (sign by side). */
export function profitUsd(symbol: string, side: 'buy' | 'sell', lots: number, open: number, close: number, quotes: Map<string, Q>): number | null {
  const uq = usdPerQuote(symbol, quotes)
  if (uq === null) return null
  return (side === 'buy' ? close - open : open - close) * lots * CONTRACT * uq
}

/** Largest volume the free margin allows (for the order panel's slider). */
export function maxLots(symbol: string, free: number, leverage: number, quotes: Map<string, Q>, cap = 50): number {
  const one = marginUsd(symbol, 1, leverage, quotes)
  if (!one || one <= 0) return cap
  return Math.max(0.01, Math.min(cap, Math.floor((free / one) * 100) / 100))
}

export const snap = (v: number, step = 0.01) => Math.round(v / step) * step
export const lots = (v: number) => Number(snap(v).toFixed(2))
