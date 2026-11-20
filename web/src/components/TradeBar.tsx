import { useState } from 'react'
import { parseLots, stepLots } from '../lib/tradeMath'
import type { OrderType } from '../lib/trade'

interface Props {
  symbol: string
  disabled: boolean
  peekOn: boolean
  message: string
  mode: 'practice' | 'trade'
  onMode: (m: 'practice' | 'trade') => void
  onSubmit: (side: 'buy' | 'sell') => void
  // trade-mode inputs
  lots: number; onLots: (v: number) => void
  slPips: number | null; tpPips: number | null; onSlTp: (sl: number | null, tp: number | null) => void
  orderType: OrderType; onOrderType: (t: OrderType) => void
  orderPrice: string; onOrderPrice: (v: string) => void
  bid: string; ask: string
  // buttons
  onToggleCalls: () => void; callsOpen: boolean; pendingCalls: number
  onToggleSheet: () => void; sheetOpen: boolean; openPositions: number
}

const numOrNull = (t: string): number | null => { const v = Number(t); return t.trim() !== '' && isFinite(v) && v > 0 ? v : null }

export function TradeBar(p: Props) {
  const [lotsText, setLotsText] = useState<string | null>(null)
  const trade = p.mode === 'trade'
  const label = trade ? (p.orderType === 'market' ? 'Market order' : `${p.orderType === 'limit' ? 'Limit' : 'Stop'} order`) : 'Practice call – no position'
  return (
    <footer className={`tradebar${trade ? ' trading' : ''}`} data-testid="tradebar">
      <div className="trade-meta">
        <span className="trade-sym">{p.symbol}</span>
        <span className={`small trade-msg ${p.peekOn ? 'peek-text' : 'dim'}`} data-testid="call-message">
          {p.message || (p.peekOn ? 'Peek on: marked peeked' : trade ? `${label} · ${p.bid} / ${p.ask}` : label)}
        </span>
        <button className={`mode ${trade ? 'on' : ''}`} data-testid="mode-toggle" aria-pressed={trade}
                onClick={() => p.onMode(trade ? 'practice' : 'trade')}>{trade ? 'TRADE' : 'PRACTICE'}</button>
      </div>
      {trade && (
        <div className="trade-inputs" data-testid="trade-inputs">
          <div className="lots">
            <button aria-label="Fewer lots" data-testid="lots-minus" onClick={() => p.onLots(stepLots(p.lots, -1))}>−</button>
            <input aria-label="Lots" data-testid="lots" inputMode="decimal" value={lotsText ?? p.lots.toFixed(2)}
                   onFocus={() => setLotsText(p.lots.toFixed(2))} onChange={(e) => setLotsText(e.target.value)}
                   onBlur={() => { const v = lotsText === null ? null : parseLots(lotsText); if (v !== null) p.onLots(v); setLotsText(null) }} />
            <button aria-label="More lots" data-testid="lots-plus" onClick={() => p.onLots(stepLots(p.lots, 1))}>+</button>
          </div>
          <input className="pips" aria-label="Stop loss in pips" data-testid="sl-pips" inputMode="decimal" placeholder="SL pips" value={p.slPips ?? ''}
                 onChange={(e) => p.onSlTp(numOrNull(e.target.value), p.tpPips)} />
          <input className="pips" aria-label="Take profit in pips" data-testid="tp-pips" inputMode="decimal" placeholder="TP pips" value={p.tpPips ?? ''}
                 onChange={(e) => p.onSlTp(p.slPips, numOrNull(e.target.value))} />
          <select aria-label="Order type" data-testid="order-type" value={p.orderType} onChange={(e) => p.onOrderType(e.target.value as OrderType)}>
            <option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option>
          </select>
          {p.orderType !== 'market' && (
            <input className="price-in" aria-label="Order price" data-testid="order-price" inputMode="decimal" placeholder="price" value={p.orderPrice} onChange={(e) => p.onOrderPrice(e.target.value)} />
          )}
        </div>
      )}
      <div className="trade-row">
        <button className="big sell" data-testid="sell" disabled={p.disabled} onClick={() => p.onSubmit('sell')}>SELL</button>
        <button className="big buy" data-testid="buy" disabled={p.disabled} onClick={() => p.onSubmit('buy')}>BUY</button>
        {trade && (
          <button className={`calls-btn${p.sheetOpen ? ' on' : ''}`} data-testid="toggle-sheet" onClick={p.onToggleSheet}>
            Trades{p.openPositions > 0 && <span className="badge">{p.openPositions}</span>}
          </button>
        )}
        <button className={`calls-btn${p.callsOpen ? ' on' : ''}`} data-testid="toggle-calls" onClick={p.onToggleCalls}>
          Calls{p.pendingCalls > 0 && <span className="badge">{p.pendingCalls}</span>}
        </button>
      </div>
    </footer>
  )
}
