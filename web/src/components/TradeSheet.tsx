import { useEffect, useState } from 'react'
import { EquityChart } from './EquityChart'
import { BotPanel, type BotPanelProps } from './BotPanel'
import { fmtMoney, fmtPips, fmtPrice, halfLots } from '../lib/tradeMath'
import { hhmmss } from '../lib/time'
import type { Deal, EquityPoint, TOrder, TPosition, TradeState } from '../lib/trade'

export type SheetTab = 'positions' | 'history' | 'equity' | 'bot'

interface Meta { digits: number; pip: number }
interface Props {
  state: TradeState | null
  history: Deal[]
  equity: EquityPoint[]
  tab: SheetTab
  onTab: (t: SheetTab) => void
  tz: string
  meta: (symbol: string) => Meta
  onClose: () => void
  onClosePosition: (id: number, volume?: number) => void
  onCloseAll: () => void
  onModifyPosition: (id: number, change: { sl?: number | null; tp?: number | null }) => void
  onModifyOrder: (id: number, change: { price?: number; sl?: number | null; tp?: number | null }) => void
  onCancelOrder: (id: number) => void
  onReset: () => void
  bot: BotPanelProps
}

const cls = (v: number | null | undefined) => (v === null || v === undefined ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : '')

/** A number box that commits on Enter / blur / the Set button; "×" clears an optional level. */
function LevelInput({ label, value, digits, step, testId, clearable, onCommit }: {
  label: string; value: number | null; digits: number; step: number; testId: string; clearable: boolean
  onCommit: (v: number | null) => void
}) {
  const [text, setText] = useState<string | null>(null)
  useEffect(() => setText(null), [value])
  const shown = text ?? (value === null ? '' : value.toFixed(digits))
  const commit = () => {
    if (text === null) return
    const t = text.trim()
    if (t === '') { if (clearable && value !== null) onCommit(null) } else { const v = Number(t); if (isFinite(v) && v > 0 && v !== value) onCommit(Number(v.toFixed(digits))) }
    setText(null)
  }
  const nudge = (dir: 1 | -1) => { if (value !== null) onCommit(Number((value + dir * step).toFixed(digits))) }
  return (
    <div className="level-in" data-testid={`${testId}-box`}>
      <span className="dim">{label}</span>
      <button aria-label={`${label} down`} disabled={value === null} onClick={() => nudge(-1)} data-testid={`${testId}-down`}>−</button>
      <input data-testid={testId} inputMode="decimal" placeholder="none" value={shown} onChange={(e) => setText(e.target.value)}
             onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} />
      <button aria-label={`${label} up`} disabled={value === null} onClick={() => nudge(1)} data-testid={`${testId}-up`}>+</button>
      {clearable && value !== null && <button aria-label={`Clear ${label}`} onClick={() => onCommit(null)} data-testid={`${testId}-clear`}>×</button>}
    </div>
  )
}

function PositionCard({ p, m, onClose, onModify }: { p: TPosition; m: Meta; onClose: Props['onClosePosition']; onModify: Props['onModifyPosition'] }) {
  const half = halfLots(p.volume)
  return (
    <li className="pos-card" data-testid="position-row" data-id={p.id} data-side={p.side}>
      <div className="pos-head">
        <b className={p.side === 'buy' ? 'buy-text' : 'sell-text'}>{p.side.toUpperCase()}</b>
        <span>{p.volume.toFixed(2)} {p.symbol}</span>
        {p.peeked ? <span className="tag peek-tag">peeked</span> : null}
        {p.source === 'bot' ? <span className="tag bot-tag" data-testid="bot-tag">bot</span> : null}
        <span className="dim small">#{p.id}</span>
        <span className={`pnl ${cls(p.profit)}`} data-testid="position-profit">{fmtMoney(p.profit, true)}</span>
      </div>
      <div className="dim small pos-line">
        <span data-testid="position-open">open {fmtPrice(p.open_price, m.digits)}</span>
        <span>now {fmtPrice(p.price, m.digits)}</span>
        <span className={cls(p.pips)} data-testid="position-pips">{fmtPips(p.pips)} pips</span>
        <span>margin {fmtMoney(p.margin)}</span>
      </div>
      <div className="levels">
        <LevelInput label="SL" value={p.sl} digits={m.digits} step={m.pip} testId={`sl-${p.id}`} clearable onCommit={(v) => onModify(p.id, { sl: v })} />
        <LevelInput label="TP" value={p.tp} digits={m.digits} step={m.pip} testId={`tp-${p.id}`} clearable onCommit={(v) => onModify(p.id, { tp: v })} />
      </div>
      <div className="pos-actions">
        <button className="act" data-testid={`close-${p.id}`} onClick={() => onClose(p.id)}>Close</button>
        <button className="act" data-testid={`close-half-${p.id}`} disabled={half === null} onClick={() => half !== null && onClose(p.id, half)}>Close ½</button>
      </div>
    </li>
  )
}

function OrderCard({ o, m, onModify, onCancel }: { o: TOrder; m: Meta; onModify: Props['onModifyOrder']; onCancel: Props['onCancelOrder'] }) {
  return (
    <li className="pos-card order" data-testid="order-row" data-id={o.id}>
      <div className="pos-head">
        <b className={o.side === 'buy' ? 'buy-text' : 'sell-text'}>{o.side.toUpperCase()} {o.type.toUpperCase()}</b>
        <span>{o.volume.toFixed(2)} {o.symbol}</span><span className="dim small">#{o.id}</span>
      </div>
      <div className="levels">
        <LevelInput label="@" value={o.price} digits={m.digits} step={m.pip} testId={`oprice-${o.id}`} clearable={false} onCommit={(v) => v !== null && onModify(o.id, { price: v })} />
        <LevelInput label="SL" value={o.sl} digits={m.digits} step={m.pip} testId={`osl-${o.id}`} clearable onCommit={(v) => onModify(o.id, { sl: v })} />
        <LevelInput label="TP" value={o.tp} digits={m.digits} step={m.pip} testId={`otp-${o.id}`} clearable onCommit={(v) => onModify(o.id, { tp: v })} />
      </div>
      <div className="pos-actions"><button className="act" data-testid={`cancel-${o.id}`} onClick={() => onCancel(o.id)}>Cancel order</button></div>
    </li>
  )
}

export function TradeSheet(p: Props) {
  const a = p.state?.account
  const wins = p.history.filter((d) => d.net > 0).length
  return (
    <section className="sheet trade-sheet" data-testid="trade-sheet">
      <div className="sheet-head">
        <div className="tabs" role="tablist">
          {(['positions', 'history', 'equity', 'bot'] as SheetTab[]).map((t) => (
            <button key={t} role="tab" aria-selected={p.tab === t} className={p.tab === t ? 'on' : ''} data-testid={`tab-${t}`} onClick={() => p.onTab(t)}>
              {t === 'positions' ? `Trade${p.state ? ` (${p.state.positions.length})` : ''}` : t === 'history' ? 'History' : t === 'equity' ? 'Equity' : `Bot${p.bot.status?.running ? ' ●' : ''}`}
            </button>
          ))}
        </div>
        <button onClick={p.onClose} aria-label="Close trades">✕</button>
      </div>
      {a && (
        <div className="acct-grid" data-testid="account-grid">
          <div><span className="dim">Balance</span><b data-testid="acct-balance">{fmtMoney(a.balance)}</b></div>
          <div><span className="dim">Equity</span><b data-testid="acct-equity">{fmtMoney(a.equity)}</b></div>
          <div><span className="dim">Margin</span><b data-testid="acct-margin">{fmtMoney(a.margin)}</b></div>
          <div><span className="dim">Free</span><b data-testid="acct-free">{fmtMoney(a.free_margin)}</b></div>
          <div><span className="dim">Level</span><b data-testid="acct-level">{a.margin_level === null ? '–' : `${a.margin_level.toFixed(0)}%`}</b></div>
          <div><span className="dim">Open P&amp;L</span><b className={cls(a.unrealized)}>{fmtMoney(a.unrealized, true)}</b></div>
        </div>
      )}
      <div className="sheet-body">
        {p.tab === 'positions' && (
          <>
            <ul className="poslist" data-testid="positions-list">
              {p.state?.positions.length === 0 && <li className="dim">No open positions. Switch to TRADE mode and tap BUY or SELL.</li>}
              {p.state?.positions.map((x) => <PositionCard key={x.id} p={x} m={p.meta(x.symbol)} onClose={p.onClosePosition} onModify={p.onModifyPosition} />)}
              {p.state?.orders.filter((o) => o.kind === 'open').map((o) => <OrderCard key={o.id} o={o} m={p.meta(o.symbol)} onModify={p.onModifyOrder} onCancel={p.onCancelOrder} />)}
            </ul>
            <div className="sheet-foot">
              <button className="act" data-testid="close-all" disabled={!p.state?.positions.length} onClick={p.onCloseAll}>Close all</button>
              <button className="act danger" data-testid="reset-account" onClick={p.onReset}>Reset account…</button>
            </div>
          </>
        )}
        {p.tab === 'history' && (
          <>
            <div className="dim small tip" data-testid="history-summary">
              {p.history.length} closed trades · {p.history.length ? `${((wins / p.history.length) * 100).toFixed(0)}% winners` : '–'} ·
              net <span className={cls(p.history.reduce((s, d) => s + d.net, 0))}>{fmtMoney(p.history.reduce((s, d) => s + d.net, 0), true)}</span> USD
            </div>
            <ul className="hist" data-testid="history-list">
              {p.history.length === 0 && <li className="dim">No closed trades yet.</li>}
              {p.history.map((d) => (
                <li key={d.id} data-testid="history-row" data-reason={d.reason} data-peeked={d.peeked}>
                  <span className="dim">{hhmmss(Math.floor(d.close_ms / 1000), p.tz)}</span>
                  <span>{d.symbol}</span>
                  <b className={d.side === 'buy' ? 'buy-text' : 'sell-text'}>{d.side.toUpperCase()} {d.volume.toFixed(2)}</b>
                  <span className="tag reason">{d.reason}</span>
                  {d.peeked ? <span className="tag peek-tag">peeked</span> : d.source === 'bot' ? <span className="tag bot-tag" data-testid="history-bot">bot</span> : <span />}
                  <span className="dim small hist-line">{fmtPrice(d.open_price, p.meta(d.symbol).digits)} → {fmtPrice(d.close_price, p.meta(d.symbol).digits)} · {Math.round(d.duration_s)}s</span>
                  <span className={cls(d.pips)}>{fmtPips(d.pips)}p</span>
                  <span className={`pnl ${cls(d.net)}`} data-testid="history-net">{fmtMoney(d.net, true)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        {p.tab === 'bot' && <BotPanel {...p.bot} />}
        {p.tab === 'equity' && (
          <>
            <EquityChart points={p.equity} tz={p.tz} />
            {a && <div className="dim small tip">Peak {fmtMoney(a.peak_equity)} · drawdown {a.drawdown_pct.toFixed(2)}% · realised {fmtMoney(a.realized, true)} · {a.deals} closed trades · leverage 1:{a.leverage}</div>}
          </>
        )}
      </div>
    </section>
  )
}
