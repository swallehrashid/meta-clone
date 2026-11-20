import { Fragment, useMemo, useState, type ReactNode } from 'react'
import type { Deal, TOrder } from '../lib/trade'
import { useT } from './ctx'
import { dayStart, dmy, money, stamp, vol } from './format'
import { EInbox, ICalendarSolid, IExchange, IReport, ISort } from './icons'
import type { PeriodKind } from './prefs'
import { Dialog, Empty, Header, IconBtn, Menu, type MenuItem } from './ui'
import { SumRow } from './Trade'

type HTab = 'positions' | 'orders' | 'deals'
interface Row { key: string; sym: string; side: string; volume: number; time: number; profit: number | null; ticket: number; type: string; node: (open: boolean) => ReactNode }

export function periodRange(kind: PeriodKind, nowMs: number, tz: string, from?: number | null, to?: number | null): [number, number] {
  const today = dayStart(nowMs, tz)
  switch (kind) {
    case 'today': return [today, Infinity]
    case 'week': return [today - 7 * 86400_000, Infinity]
    case 'month': return [today - 30 * 86400_000, Infinity]
    case '3months': return [today - 91 * 86400_000, Infinity]
    default: return [from ?? 0, to ?? Infinity]
  }
}

export function HistoryScreen() {
  const t = useT()
  const p = t.prefs
  const [tab, setTab] = useState<HTab>('positions')
  const [sym, setSym] = useState<string | null>(null)
  const [sort, setSort] = useState<{ k: string; up: boolean }>({ k: 'time', up: true })
  const [open, setOpen] = useState<string | null>(null)
  const [menu, setMenu] = useState<{ items: MenuItem[]; at: { top?: number; left?: number; right?: number } } | null>(null)
  const [custom, setCustom] = useState(false)
  const tz = t.cfg.tz
  const now = Date.now() - t.cfg.delay_s * 1000                       // the terminal's clock is the delayed one
  const [lo, hi] = periodRange(p.period, now, tz, p.periodFrom, p.periodTo)
  const inP = (ms: number) => ms >= lo && ms <= hi
  const a = t.trade?.account
  const fx = (s: string, x: number | null | undefined) => (x === null || x === undefined ? '–' : x.toFixed(t.digitsOf(s)))
  const deals = useMemo(() => t.deals.filter((d) => inP(d.close_ms) && (!sym || d.symbol === sym)), [t.deals, lo, hi, sym])     // eslint-disable-line react-hooks/exhaustive-deps
  const allOrders = useMemo(() => t.ordersHistory.filter((o) => inP(o.done_ms ?? o.placed_ms) && (!sym || o.symbol === sym)), [t.ordersHistory, lo, hi, sym]) // eslint-disable-line react-hooks/exhaustive-deps
  const depositMs = useMemo(() => {
    const ts = [...t.deals.map((d) => d.open_ms), ...t.ordersHistory.map((o) => o.placed_ms)]
    return ts.length ? Math.min(...ts) - 1000 : (a?.t_engine_ms || now)
  }, [t.deals, t.ordersHistory, a, now])
  const depIn = inP(depositMs) && !sym
  const profit = deals.reduce((s, d) => s + d.gross, 0), commission = -deals.reduce((s, d) => s + d.commission, 0)
  const deposit = depIn ? a?.initial_balance ?? 0 : 0
  const showProfit = (d: Deal) => (p.profitInPoints ? `${d.pips >= 0 ? '' : '-'}${Math.abs(Math.round(d.pips * 10))} pts` : money(d.gross))
  const pl = (v: number | null, text?: string) => <div className={`pl ${v === null ? '' : v < 0 ? 'neg' : v > 0 ? 'pos' : ''}`}>{text ?? money(v)}</div>
  const opp = (s: string) => (s === 'buy' ? 'sell' : 'buy')
  const det = (cells: [string, string][]) => <div className="det">{cells.map(([k, v]) => <Fragment key={k}><span>{k}</span><b>{v}</b></Fragment>)}</div>
  const reasonBar = (r: string) => (r === 'sl' || r === 'stopout' ? '#f08a24' : r === 'tp' ? '#36b24a' : 'transparent')

  const rows: Row[] = useMemo(() => {
    if (tab === 'positions') return deals.map((d) => ({
      key: `p${d.id}`, sym: d.symbol, side: d.side, volume: d.volume, time: d.close_ms, profit: d.gross, ticket: d.position_id, type: d.side,
      node: (o: boolean) => <>
        <span className="bar" style={{ background: reasonBar(d.reason) }} />
        <div className="main2"><div className="t1">{d.symbol}, <span className={d.side}>{d.side} {vol(d.volume)}</span></div>
          <div className="t2">{fx(d.symbol, d.open_price)} → {fx(d.symbol, d.close_price)}</div></div>
        <div className="rt"><div className="when">{stamp(d.close_ms, tz)}</div>{pl(d.gross, showProfit(d))}</div>
        {o && det([['Open:', stamp(d.open_ms, tz)], ['S/L:', fx(d.symbol, d.sl)], ['Close:', stamp(d.close_ms, tz)], ['T/P:', fx(d.symbol, d.tp)],
                   ['Swap:', '0.00'], ['Commission:', money(-d.commission)], ['Position:', `#${d.position_id}`], ['Reason:', d.reason]])}
      </> }))
    if (tab === 'deals') return deals.flatMap((d) => [
      { key: `dc${d.id}`, sym: d.symbol, side: opp(d.side), volume: d.volume, time: d.close_ms, profit: d.gross, ticket: d.id * 2, type: 'out',
        node: (o: boolean) => <>
          <div className="main2"><div className="t1">{d.symbol}, <span className={opp(d.side)}>{opp(d.side)} out</span></div>
            <div className="t2">{vol(d.volume)} at {fx(d.symbol, d.close_price)}</div></div>
          <div className="rt"><div className="when">{stamp(d.close_ms, tz)}</div>{pl(d.gross, showProfit(d))}</div>
          {o && det([['Deal:', `#${d.id * 2}`], ['Order:', `#${d.position_id}`], ['Commission:', money(-d.commission / 2)], ['Swap:', '0.00'], ['Reason:', d.reason], ['Position:', `#${d.position_id}`]])}
        </> },
      { key: `do${d.id}`, sym: d.symbol, side: d.side, volume: d.volume, time: d.open_ms, profit: 0, ticket: d.id * 2 - 1, type: 'in',
        node: (o: boolean) => <>
          <div className="main2"><div className="t1">{d.symbol}, <span className={d.side}>{d.side} in</span></div>
            <div className="t2">{vol(d.volume)} at {fx(d.symbol, d.open_price)}</div></div>
          <div className="rt"><div className="when">{stamp(d.open_ms, tz)}</div>{pl(0)}</div>
          {o && det([['Deal:', `#${d.id * 2 - 1}`], ['Order:', `#${d.position_id}`], ['Commission:', money(-d.commission / 2)], ['Position:', `#${d.position_id}`]])}
        </> },
    ])
    // orders: every real order (filled / cancelled / expired / rejected) + the closing order of each deal
    const real = allOrders.map((o: TOrder) => ({
      key: `o${o.id}`, sym: o.symbol, side: o.side, volume: o.volume, time: o.done_ms ?? o.placed_ms, profit: null, ticket: o.id, type: o.type,
      node: (op: boolean) => <>
        <div className="main2"><div className="t1">{o.symbol}, <span className={o.side}>{o.side}{o.type !== 'market' ? ' ' + o.type.replace('_', ' ') : ''}</span></div>
          <div className="t2">{vol(o.volume)} / {o.status === 'filled' ? vol(o.volume) : '0'} {o.price !== null ? `at ${fx(o.symbol, o.price)}` : 'at market'}</div></div>
        <div className="rt"><div className="when">{stamp(o.done_ms ?? o.placed_ms, tz)}</div><div className="pl" style={{ fontWeight: 400, fontSize: 14, color: o.status === 'filled' ? 'var(--text2)' : 'var(--dim)' }}>{o.status}</div></div>
        {op && det([['Order:', `#${o.id}`], ['Placed:', stamp(o.placed_ms, tz)], ['S/L:', fx(o.symbol, o.sl)], ['T/P:', fx(o.symbol, o.tp)], ['Comment:', o.comment || (o.reason ?? '') || '–'], ['Done:', o.done_ms ? stamp(o.done_ms, tz) : '–']])}
      </> }))
    const closing = deals.map((d) => ({
      key: `oc${d.id}`, sym: d.symbol, side: opp(d.side), volume: d.volume, time: d.close_ms, profit: null, ticket: d.position_id + 0.5, type: 'market',
      node: (op: boolean) => <>
        <div className="main2"><div className="t1">{d.symbol}, <span className={opp(d.side)}>{opp(d.side)}</span></div>
          <div className="t2">{vol(d.volume)} / {vol(d.volume)} at {fx(d.symbol, d.close_price)}</div></div>
        <div className="rt"><div className="when">{stamp(d.close_ms, tz)}</div><div className="pl" style={{ fontWeight: 400, fontSize: 14, color: 'var(--text2)' }}>filled</div></div>
        {op && det([['Closes:', `#${d.position_id}`], ['Reason:', d.reason]])}
      </> }))
    return [...real, ...closing]
  }, [tab, deals, allOrders, p.profitInPoints, tz])                  // eslint-disable-line react-hooks/exhaustive-deps

  const sorted = useMemo(() => {
    const r = [...rows]
    const k = sort.k
    r.sort((x, y) => k === 'symbol' ? x.sym.localeCompare(y.sym) : k === 'type' ? x.side.localeCompare(y.side) : k === 'volume' ? x.volume - y.volume
      : k === 'profit' ? (x.profit ?? 0) - (y.profit ?? 0) : k === 'ticket' || k === 'deal' || k === 'order' ? x.ticket - y.ticket : x.time - y.time)
    return sort.up ? r : r.reverse()
  }, [rows, sort])

  const sortKeys: Record<HTab, [string, string][]> = {
    positions: [['symbol', 'Symbol'], ['ticket', 'Ticket'], ['type', 'Type'], ['volume', 'Volume'], ['time', 'Open time'], ['close', 'Close time'], ['profit', 'Profit']],
    orders: [['symbol', 'Symbol'], ['order', 'Order'], ['type', 'Type'], ['volume', 'Volume'], ['time', 'Open time'], ['close', 'Close time'], ['state', 'State']],
    deals: [['symbol', 'Symbol'], ['deal', 'Deal'], ['type', 'Type'], ['volume', 'Volume'], ['time', 'Time'], ['profit', 'Profit']],
  }
  const sortMenu = () => setMenu({ at: { top: 52, right: 110 }, items: sortKeys[tab].map(([k, l]) => ({
    label: l, sort: sort.k === k || (k === 'close' && sort.k === 'time' && false) ? (sort.up ? 'up' : 'down') : null, testId: `hsort-${k}`,
    onClick: () => setSort((s) => (s.k === (k === 'close' || k === 'state' ? 'time' : k) ? { k: s.k, up: !s.up } : { k: k === 'close' || k === 'state' ? 'time' : k, up: true })) })) })
  const periodMenu = () => {
    const today = dayStart(now, tz), d = (n: number) => dmy(today - n * 86400_000, tz)
    const it = (k: PeriodKind, label: string, sub: string, n: string): MenuItem => ({ label, sub, icon: <ICalendarSolid size={26} n={n} />, checked: p.period === k, testId: `period-${k}`,
      onClick: () => (k === 'custom' ? setCustom(true) : t.setPrefs({ period: k })) })
    setMenu({ at: { top: 52, left: 70 }, items: [
      it('today', 'Today', dmy(now, tz), ''), it('week', 'Last week', `${d(7)} - ${dmy(now, tz)}`, ''), it('month', 'Last month', `${d(30)} - ${dmy(now, tz)}`, ''),
      it('3months', 'Last 3 months', `${d(91)} - ${dmy(now, tz)}`, '3'), it('custom', 'Custom period', p.period === 'custom' && p.periodFrom ? `${dmy(p.periodFrom, tz)} - ${dmy(p.periodTo ?? now, tz)}` : '', ''),
    ] })
  }
  const symMenu = () => setMenu({ at: { top: 52, left: 50 }, items: [
    { label: 'All symbols', checked: !sym, onClick: () => setSym(null) },
    ...[...new Set([...t.allSymbols, ...t.deals.map((d) => d.symbol)])].map((s) => ({ label: s, checked: sym === s, onClick: () => setSym(s) })),
  ] })
  const tabs: HTab[] = ['positions', 'orders', 'deals']
  const balanceRow = depIn && (
    <div className="row" key="bal" onClick={() => setOpen(open === 'bal' ? null : 'bal')} data-testid="h-balance">
      <div className="main2"><div className="t1">Balance</div></div>
      <div className="rt"><div className="when">{stamp(depositMs, tz)}</div><div className="pl pos" style={{ fontWeight: 400 }}>{money(deposit)}</div></div>
      {open === 'bal' && det([['Comment:', 'Initial deposit'], ['Deal:', '#1'], ['Type:', 'balance'], ['Currency:', 'USD']])}
    </div>
  )
  const filled = allOrders.filter((o) => o.status === 'filled').length + (tab === 'orders' ? deals.length : 0)
  const cancelled = allOrders.filter((o) => o.status !== 'filled').length

  return (
    <div className="main" data-testid="history-screen">
      <Header left="menu" onLeft={t.openDrawer} title="History" sub={<span className="chip" onClick={symMenu} data-testid="h-symbols">{sym ?? 'All symbols'}</span>}>
        <IconBtn onClick={() => { t.setPrefs({ profitInPoints: !p.profitInPoints }); t.toast(p.profitInPoints ? 'Profit shown in money' : 'Profit shown in points') }} label="Profit in money / points" testId="h-money"><IExchange /></IconBtn>
        <IconBtn onClick={sortMenu} label="Sort" testId="h-sort"><ISort /></IconBtn>
        <IconBtn onClick={periodMenu} label="Period" testId="h-period"><ICalendarSolid size={28} n={p.period === '3months' ? '3' : undefined} /></IconBtn>
        <IconBtn onClick={() => t.push({ id: 'report' })} label="Report" testId="h-report"><IReport /></IconBtn>
      </Header>
      <div className="tabs">
        {tabs.map((x) => <button key={x} className={tab === x ? 'on' : ''} onClick={() => { setTab(x); setOpen(null) }} data-testid={`htab-${x}`}>{x}</button>)}
        <span className="ink" style={{ width: '33.333%', left: `${tabs.indexOf(tab) * 33.333}%` }} />
      </div>
      <div className="scroll" data-testid="history">
        {tab !== 'orders' ? (
          <>
            <div className="sum" style={{ paddingTop: 10 }}>
              <SumRow k="Profit" v={money(profit)} cls={profit < 0 ? 'neg' : 'pos'} /><SumRow k="Deposit" v={money(deposit)} />
              <SumRow k="Swap" v="0.00" /><SumRow k="Commission" v={money(commission)} /><SumRow k="Balance" v={money(deposit + profit + commission)} />
            </div>
            <div className="rule" />
            {sort.up && balanceRow}
            {sorted.map((r) => <div className="row" key={r.key} onClick={() => setOpen(open === r.key ? null : r.key)} data-testid={`h-${r.key}`}>{r.node(open === r.key)}</div>)}
            {!sort.up && balanceRow}
          </>
        ) : sorted.length ? (
          <>
            <div className="sum" style={{ paddingTop: 10 }}><SumRow k="Filled" v={String(filled)} /><SumRow k="Canceled" v={String(cancelled)} /><SumRow k="Total" v={String(filled + cancelled)} /></div>
            <div className="rule" />
            {sorted.map((r) => <div className="row" key={r.key} onClick={() => setOpen(open === r.key ? null : r.key)} data-testid={`h-${r.key}`}>{r.node(open === r.key)}</div>)}
          </>
        ) : <Empty icon={<EInbox />} text="Empty history" />}
      </div>
      {menu && <Menu items={menu.items} at={menu.at} onClose={() => setMenu(null)} wide={menu.items.some((i) => i.icon)} testId="history-menu" />}
      {custom && <CustomPeriod tz={tz} from={p.periodFrom ?? dayStart(now, tz) - 7 * 86400_000} to={p.periodTo ?? now}
        onCancel={() => setCustom(false)} onSet={(f, to) => { t.setPrefs({ period: 'custom', periodFrom: f, periodTo: to }); setCustom(false) }} />}
    </div>
  )
}

function CustomPeriod({ from, to, onSet, onCancel }: { tz: string; from: number; to: number; onSet: (f: number, t: number) => void; onCancel: () => void }) {
  const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10)
  const [f, setF] = useState(iso(from)), [tt, setT] = useState(iso(to))
  return (
    <Dialog title="Custom period" onClose={onCancel} testId="custom-period" actions={[{ label: 'Cancel', onClick: onCancel },
      { label: 'OK', primary: true, testId: 'period-ok', onClick: () => onSet(new Date(f + 'T00:00:00Z').getTime(), new Date(tt + 'T23:59:59Z').getTime()) }]}>
      <div>From</div><input type="date" value={f} onChange={(e) => setF(e.target.value)} data-testid="period-from" />
      <div style={{ marginTop: 14 }}>To</div><input type="date" value={tt} onChange={(e) => setT(e.target.value)} data-testid="period-to" />
    </Dialog>
  )
}
