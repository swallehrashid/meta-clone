import { useMemo, useState } from 'react'
import type { TOrder, TPosition } from '../lib/trade'
import { useT } from './ctx'
import { money, stamp, vol } from './format'
import { IDocPlus, ISort } from './icons'
import { Header, IconBtn, Menu, longPress, type MenuItem } from './ui'

type SortKey = 'order' | 'time' | 'symbol' | 'profit'

export function SumRow({ k, v, cls = '' }: { k: string; v: string; cls?: string }) {
  return <div className="sumrow"><span>{k}:</span><span className="dots" /><span className={`val ${cls}`}>{v}</span></div>
}

export function TradeScreen() {
  const t = useT()
  const [sort, setSort] = useState<{ k: SortKey; up: boolean }>({ k: 'time', up: true })
  const [open, setOpen] = useState<string | null>(null)
  const [menu, setMenu] = useState<{ items: MenuItem[]; at: { top?: number; right?: number; left?: number } } | null>(null)
  const a = t.trade?.account
  const fx = (s: string, x: number | null | undefined) => (x === null || x === undefined ? '–' : x.toFixed(t.digitsOf(s)))
  const order = <X extends { id: number; symbol: string },>(arr: X[], time: (x: X) => number, profit: (x: X) => number) => {
    const r = [...arr]
    const f = sort.k === 'order' ? (x: X) => x.id : sort.k === 'symbol' ? null : sort.k === 'profit' ? profit : time
    if (f) r.sort((x, y) => f(x) - f(y)); else r.sort((x, y) => x.symbol.localeCompare(y.symbol))
    return sort.up ? r : r.reverse()
  }
  const positions = useMemo(() => order(t.trade?.positions ?? [], (x) => x.open_ms, (x) => x.profit ?? 0), [t.trade, sort])  // eslint-disable-line react-hooks/exhaustive-deps
  const pending = useMemo(() => order((t.trade?.orders ?? []).filter((o) => o.kind === 'open'), (x) => x.placed_ms, () => 0), [t.trade, sort])  // eslint-disable-line react-hooks/exhaustive-deps

  const at = (y: number) => {
    const host = document.querySelector('.mt5')!.getBoundingClientRect()
    return { top: Math.min(Math.max(60, y - host.top), host.height - 330), left: 40 }
  }
  const posMenu = (x: TPosition, y: number) => setMenu({ at: at(y), items: [
    { label: 'Close position', onClick: () => t.setPanel({ kind: 'close', positionId: x.id }), testId: 'pm-close' },
    { label: 'Modify position', onClick: () => t.setPanel({ kind: 'modify', positionId: x.id }), testId: 'pm-modify' },
    { label: 'Trade', onClick: () => t.setPanel({ kind: 'new', symbol: x.symbol, expanded: true }), testId: 'pm-trade' },
    { label: 'Chart', onClick: () => { t.setChartSym(x.symbol); t.setTab('chart') }, testId: 'pm-chart' },
    { label: 'Bulk Operations', onClick: () => setTimeout(() => bulk(y), 0), testId: 'pm-bulk' },
  ] })
  const ordMenu = (o: TOrder, y: number) => setMenu({ at: at(y), items: [
    { label: 'Modify order', onClick: () => t.setPanel({ kind: 'order', orderId: o.id }), testId: 'om-modify' },
    { label: 'Delete order', onClick: () => { void t.cancelOrd(o.id) }, testId: 'om-delete' },
    { label: 'Chart', onClick: () => { t.setChartSym(o.symbol); t.setTab('chart') } },
  ] })
  const closeWhere = async (f: (x: TPosition) => boolean, what: string) => {
    const list = (t.trade?.positions ?? []).filter(f)
    if (!list.length) { t.toast(`No ${what}`); return }
    for (const x of list) await t.closePos(x.id)
  }
  const bulk = (y: number) => setMenu({ at: at(y), items: [
    { label: 'Close all positions', onClick: () => closeWhere(() => true, 'open positions'), testId: 'bulk-all' },
    { label: 'Close profitable positions', onClick: () => closeWhere((x) => (x.profit ?? 0) > 0, 'profitable positions') },
    { label: 'Close losing positions', onClick: () => closeWhere((x) => (x.profit ?? 0) < 0, 'losing positions') },
    { label: 'Close Buy positions', onClick: () => closeWhere((x) => x.side === 'buy', 'buy positions') },
    { label: 'Close Sell positions', onClick: () => closeWhere((x) => x.side === 'sell', 'sell positions') },
    { label: 'Delete all orders', onClick: async () => { const l = t.trade?.orders.filter((o) => o.kind === 'open') ?? []; if (!l.length) t.toast('No pending orders'); for (const o of l) await t.cancelOrd(o.id) } },
  ] })
  const sortMenu = () => setMenu({ at: { top: 52, right: 70 }, items: (['order', 'time', 'symbol', 'profit'] as SortKey[]).map((k) => ({
    label: k[0].toUpperCase() + k.slice(1), sort: sort.k === k ? (sort.up ? 'up' : 'down') : null, testId: `sort-${k}`,
    onClick: () => setSort((s) => (s.k === k ? { k, up: !s.up } : { k, up: true })) })) })

  return (
    <div className="main" data-testid="trade-screen">
      <Header left="menu" onLeft={t.openDrawer} title="Trade">
        <IconBtn onClick={sortMenu} label="Sort" testId="trade-sort"><ISort /></IconBtn>
        <IconBtn onClick={() => t.setPanel({ kind: 'new', symbol: t.chartSym, expanded: true })} label="New order" testId="new-order"><IDocPlus /></IconBtn>
      </Header>
      <div className="scroll" data-testid="trade">
        {a && (
          <div className="sum">
            <SumRow k="Balance" v={money(a.balance)} />
            <SumRow k="Equity" v={money(a.equity)} />
            {a.margin > 0 && <SumRow k="Margin" v={money(a.margin)} />}
            <SumRow k="Free margin" v={money(a.free_margin)} />
            {a.margin_level !== null && <SumRow k="Margin level" v={`${a.margin_level.toFixed(2)}%`} />}
          </div>
        )}
        <div className="rule" />
        {positions.length > 0 && <div className="seclbl">Positions</div>}
        {positions.map((x) => {
          const k = `p${x.id}`, pr = x.profit ?? 0
          const lp = longPress(() => setOpen(open === k ? null : k), (pt) => posMenu(x, pt.y))
          return (
            <div className="row" key={k} {...lp} data-testid={`pos-${x.id}`}>
              <div className="main2">
                <div className="t1">{x.symbol}, <span className={x.side}>{x.side} {vol(x.volume)}</span></div>
                <div className="t2">{fx(x.symbol, x.open_price)} → {fx(x.symbol, x.price)}</div>
              </div>
              <div className="rt"><div className={`pl ${pr < 0 ? 'neg' : pr > 0 ? 'pos' : ''}`}>{money(pr)}</div></div>
              {open === k && (
                <div className="det" data-testid={`det-${x.id}`}>
                  <span>S/L:</span><b>{fx(x.symbol, x.sl)}</b><span>Swap:</span><b>0.00</b>
                  <span>T/P:</span><b>{fx(x.symbol, x.tp)}</b><span>Commission:</span><b>{money(-x.commission / 2)}</b>
                  <span>Open:</span><b style={{ gridColumn: 'span 3', textAlign: 'left' }}>{stamp(x.open_ms, t.cfg.tz)}</b>
                  <span>ID:</span><b style={{ gridColumn: 'span 3', textAlign: 'left' }}>#{x.id}</b>
                </div>
              )}
            </div>
          )
        })}
        {pending.length > 0 && <div className="seclbl">Orders</div>}
        {pending.map((o) => {
          const k = `o${o.id}`
          const lp = longPress(() => setOpen(open === k ? null : k), (pt) => ordMenu(o, pt.y))
          return (
            <div className="row" key={k} {...lp} data-testid={`ord-${o.id}`}>
              <div className="main2">
                <div className="t1">{o.symbol}, <span className={o.side}>{o.side} {o.type.replace('_', ' ')} {vol(o.volume)}</span></div>
                <div className="t2">{vol(o.volume)} / 0 at {fx(o.symbol, o.price)}{o.stoplimit ? ` (${fx(o.symbol, o.stoplimit)})` : ''}</div>
              </div>
              <div className="rt"><div className="pl" style={{ color: 'var(--dim)', fontWeight: 400, fontSize: 13 }}>placed</div></div>
              {open === k && (
                <div className="det">
                  <span>S/L:</span><b>{fx(o.symbol, o.sl)}</b><span>T/P:</span><b>{fx(o.symbol, o.tp)}</b>
                  <span>Placed:</span><b style={{ gridColumn: 'span 3', textAlign: 'left' }}>{stamp(o.placed_ms, t.cfg.tz)}</b>
                  <span>Expires:</span><b style={{ gridColumn: 'span 3', textAlign: 'left' }}>{o.expire_ms ? stamp(o.expire_ms, t.cfg.tz) : 'GTC'}</b>
                  <span>ID:</span><b style={{ gridColumn: 'span 3', textAlign: 'left' }}>#{o.id}</b>
                </div>
              )}
            </div>
          )
        })}
      </div>
      {menu && <Menu items={menu.items} at={menu.at} onClose={() => setMenu(null)} wide={menu.items.length > 4} testId="trade-menu" />}
    </div>
  )
}
