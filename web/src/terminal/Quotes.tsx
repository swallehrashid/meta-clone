import { useRef, useState } from 'react'
import { useT, type QuoteView } from './ctx'
import { hms, priceParts, signed, stamp } from './format'
import { EQuotes, IFolder, IHome, IPencil, IPlus, ISearch, ISpread, ITrash, ICheck, IDrag } from './icons'
import { Check, Empty, Header, IconBtn, Menu, Page, type MenuItem } from './ui'

export function Px({ v, digits, dir }: { v: number; digits: number; dir?: 'up' | 'down' | 'stale' }) {
  const p = priceParts(v, digits)
  return <span className={`px ${dir ?? ''}`}><span className="pre">{p.pre}</span><span className="big">{p.big}</span><span className="sup">{p.sup}</span></span>
}

export function QuotesScreen() {
  const t = useT()
  const [menu, setMenu] = useState<{ items: MenuItem[]; at: { top: number; left?: number; right?: number } } | null>(null)
  const rows = t.symbols.map((s) => t.quotes.get(s)).filter(Boolean) as QuoteView[]
  const open = (q: QuoteView, y: number) => {
    const host = document.querySelector('.mt5')!.getBoundingClientRect()
    setMenu({ at: { top: Math.min(Math.max(60, y - host.top), host.height - 340), left: 40 }, items: [
      { label: 'New order', onClick: () => t.setPanel({ kind: 'new', symbol: q.symbol, expanded: true }), testId: 'qm-order' },
      { label: 'Chart', onClick: () => { t.setChartSym(q.symbol); t.setTab('chart') }, testId: 'qm-chart' },
      { label: 'Properties', onClick: () => t.push({ id: 'properties', symbol: q.symbol }), testId: 'qm-props' },
      { label: 'Market statistics', onClick: () => t.push({ id: 'statistics', symbol: q.symbol }), testId: 'qm-stats' },
      { label: t.prefs.advanced ? 'Simple view mode' : 'Advanced view mode', onClick: () => t.setPrefs({ advanced: !t.prefs.advanced }), testId: 'qm-mode' },
    ] })
  }
  return (
    <div className="main" data-testid="quotes-screen">
      <Header left="menu" onLeft={t.openDrawer} title="Quotes">
        <IconBtn onClick={() => t.push({ id: 'addSymbol' })} label="Add symbol" testId="q-add"><IPlus /></IconBtn>
        <IconBtn onClick={() => t.push({ id: 'selectedSymbols' })} label="Edit" testId="q-edit"><IPencil /></IconBtn>
      </Header>
      <div className="scroll" data-testid="quotes">
        {!t.prefs.advanced && rows.length > 0 && <div className="qhead"><span>Symbol</span><span>Bid</span><span>Ask</span></div>}
        {rows.map((q) => {
          const dir = (up: boolean): 'up' | 'down' | 'stale' => (q.stale ? 'stale' : up ? 'up' : 'down')
          if (!t.prefs.advanced) return (
            <div className="qs" key={q.symbol} data-symbol={q.symbol} onClick={(e) => open(q, e.clientY)}>
              <span className="sym">{q.symbol}</span><Px v={q.bid} digits={q.digits} dir={dir(q.bidUp)} /><Px v={q.ask} digits={q.digits} dir={dir(q.askUp)} />
            </div>
          )
          return (
            <div className="q" key={q.symbol} data-symbol={q.symbol} onClick={(e) => open(q, e.clientY)}>
              <div className="l">
                <div className="chg">{signed(q.change_pts)} <span className={q.change_pts < 0 ? 'neg' : q.change_pts > 0 ? 'pos' : ''}>{signed(q.change_pct, 2)}%</span></div>
                <div className="sym">{q.symbol}</div>
                <div className="meta">{hms(q.t_ms, t.cfg.tz)} <ISpread /> {q.spread_pts}</div>
              </div>
              <div className="r">
                <div className="side"><Px v={q.bid} digits={q.digits} dir={dir(q.bidUp)} /><div className="hl">L: {q.low.toFixed(q.digits)}</div></div>
                <div className="side"><Px v={q.ask} digits={q.digits} dir={dir(q.askUp)} /><div className="hl">H: {q.high.toFixed(q.digits)}</div></div>
              </div>
            </div>
          )
        })}
        {!rows.length && (t.symbols.length ? <Empty icon={<EQuotes />} text="Loading quotes…" /> : <Empty icon={<EQuotes />} text="No symbols. Tap + to add one." />)}
      </div>
      {menu && <Menu items={menu.items} at={menu.at} onClose={() => setMenu(null)} wide testId="quote-menu" />}
    </div>
  )
}

/** "+" in Quotes: search and the symbol folders. */
export function AddSymbolPage({ onBack }: { onBack: () => void }) {
  const t = useT()
  const [q, setQ] = useState('')
  const shown = new Set(t.symbols)
  const hits = q.trim() ? t.allSymbols.filter((s) => (s + ' ' + t.describe(s)).toLowerCase().includes(q.trim().toLowerCase())) : []
  const folders = [{ name: 'Forex', n: t.allSymbols.length, on: t.allSymbols.filter((s) => shown.has(s)).length }, { name: 'Indexes', n: 0, on: 0 }, { name: 'Metals', n: 0, on: 0 }]
  return (
    <Page title="Add symbol" onBack={onBack} testId="add-symbol">
      <div className="search"><ISearch size={22} /><input placeholder="Find symbols" value={q} onChange={(e) => setQ(e.target.value)} data-testid="symbol-search" /></div>
      {q.trim() ? (
        <div className="list">{hits.map((s) => <SymbolToggle key={s} s={s} />)}{!hits.length && <div className="seclbl">No symbols found</div>}</div>
      ) : (
        <div className="list">
          <div className="crumb" style={{ minHeight: 44, display: 'flex', alignItems: 'center', padding: '0 16px', borderBottom: '1px solid var(--line)' }}><IHome size={22} /></div>
          {folders.map((f) => (
            <button key={f.name} className="li" onClick={() => (f.n ? t.push({ id: 'folder', folder: f.name }) : t.toast(`${f.name}: no symbols on this server`))} data-testid={`folder-${f.name}`}>
              <IFolder /><span className="lt"><b>{f.name}</b></span><span className="lr">{f.on}/{f.n}</span>
            </button>
          ))}
        </div>
      )}
    </Page>
  )
}

function SymbolToggle({ s }: { s: string }) {
  const t = useT()
  const on = t.symbols.includes(s)
  const flip = () => {
    if (on) t.setPrefs({ hidden: [...t.prefs.hidden, s] })
    else t.setPrefs({ hidden: t.prefs.hidden.filter((x) => x !== s), order: t.prefs.order.includes(s) ? t.prefs.order : [...t.prefs.order, s] })
    t.toast(on ? `${s} removed from Quotes` : `${s} added to Quotes`)
  }
  return (
    <button className="li" onClick={flip} data-testid={`sym-${s}`}>
      <span className="lt"><b>{s}</b><small>{t.describe(s)}</small></span>
      <span style={{ color: on ? 'var(--blue)' : 'var(--dim)' }}>{on ? <ICheck size={22} /> : <IPlus size={22} />}</span>
    </button>
  )
}

export function FolderPage({ folder, onBack }: { folder: string; onBack: () => void }) {
  const t = useT()
  return <Page title={folder} onBack={onBack} testId="folder-page"><div className="list">{t.allSymbols.map((s) => <SymbolToggle key={s} s={s} />)}</div></Page>
}

/** Pencil in Quotes: reorder with the handles, delete with the bin. */
export function SelectedSymbolsPage({ onBack }: { onBack: () => void }) {
  const t = useT()
  const [del, setDel] = useState<Set<string> | null>(null)
  const [order, setOrder] = useState<string[]>(t.symbols)
  const drag = useRef<{ sym: string; y0: number; idx: number } | null>(null)
  const [dy, setDy] = useState(0)
  const ROW = 58
  const commit = (o: string[]) => { setOrder(o); t.setPrefs({ order: [...o, ...t.prefs.order.filter((x) => !o.includes(x))] }) }
  const remove = () => {
    if (!del?.size) { setDel(null); return }
    t.setPrefs({ hidden: [...new Set([...t.prefs.hidden, ...del])] })
    setOrder(order.filter((s) => !del.has(s)))
    t.toast(`${del.size} symbol${del.size > 1 ? 's' : ''} deleted`)
    setDel(null)
  }
  return (
    <Page title="Selected symbols" onBack={onBack} testId="selected-symbols" actions={<>
      <IconBtn onClick={() => t.push({ id: 'addSymbol' })} label="Add symbol"><IPlus /></IconBtn>
      <IconBtn onClick={() => (del ? remove() : setDel(new Set()))} label="Delete" testId="sel-trash" active={!!del}><ITrash /></IconBtn>
    </>}>
      <div className="list">
        {order.map((s, i) => {
          const dragging = drag.current?.sym === s
          return (
            <div key={s} className="li" style={dragging ? { transform: `translateY(${dy}px)`, background: 'var(--bg2)', position: 'relative', zIndex: 2 } : undefined}
                 onClick={() => { const n = new Set(del ?? []); if (n.has(s)) n.delete(s); else n.add(s); setDel(n) }} data-testid={`sel-${s}`}>
              {del && <Check on={del.has(s)} />}
              <span className="lt"><b>{s}</b><small>{t.describe(s)}</small></span>
              {!del && (
                <span style={{ color: 'var(--text2)', touchAction: 'none', padding: 8, cursor: 'grab' }} data-testid={`drag-${s}`} onClick={(e) => e.stopPropagation()}
                      onPointerDown={(e) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); drag.current = { sym: s, y0: e.clientY, idx: i }; setDy(0) }}
                      onPointerMove={(e) => { if (drag.current) setDy(e.clientY - drag.current.y0) }}
                      onPointerUp={(e) => {
                        const dr = drag.current
                        drag.current = null
                        if (!dr) return
                        const to = Math.max(0, Math.min(order.length - 1, dr.idx + Math.round((e.clientY - dr.y0) / ROW)))
                        setDy(0)
                        if (to !== dr.idx) { const o = [...order]; o.splice(dr.idx, 1); o.splice(to, 0, s); commit(o) }
                      }}><IDrag /></span>
              )}
            </div>
          )
        })}
      </div>
      {del && <div className="seclbl">Select symbols, then tap the bin again to delete them.</div>}
    </Page>
  )
}

export function PropertiesPage({ symbol, onBack }: { symbol: string; onBack: () => void }) {
  const t = useT()
  const q = t.quotes.get(symbol)
  const digits = t.digitsOf(symbol)
  const rows: [string, string][] = [
    ['Description', t.describe(symbol)], ['Digits', String(digits)], ['Contract size', '100 000'], ['Spread', q ? `floating (${q.spread_pts})` : 'floating'],
    ['Stops level', '0'], ['Margin currency', symbol.slice(0, 3)], ['Profit currency', symbol.slice(3, 6)], ['Calculation', 'Forex'],
    ['Chart mode', 'By bid price'], ['Trade', 'Full access'], ['Execution', 'Market'], ['GTC mode', 'Good till cancelled'], ['Filling', 'Fill or Kill'],
    ['Expiration', 'GTC, Today, Specified'], ['Orders', 'Market, Limit, Stop, Stop limit, Stop loss, Take profit'], ['Minimal volume', '0.01'],
    ['Maximal volume', '50'], ['Volume step', '0.01'], ['Swap', 'none (no swaps on this server)'], ['Quotes delay', `${t.cfg.delay_s} s behind real time`],
  ]
  return (
    <Page title={symbol} sub="Specification" onBack={onBack} testId="properties">
      <div className="art" style={{ padding: '6px 0' }}>{rows.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, padding: '10px 16px', borderBottom: '1px solid var(--line)' }}>
          <span style={{ color: 'var(--dim)' }}>{k}</span><span style={{ textAlign: 'right', color: 'var(--text)' }}>{v}</span>
        </div>))}</div>
    </Page>
  )
}

export function StatisticsPage({ symbol, onBack }: { symbol: string; onBack: () => void }) {
  const t = useT()
  const q = t.quotes.get(symbol)
  const f = (x?: number) => (x === undefined || !q ? '–' : x.toFixed(q.digits))
  const rows: [string, string][] = q ? [
    ['Bid', f(q.bid)], ['Ask', f(q.ask)], ['Spread', `${q.spread_pts} points`], ['Daily low', f(q.low)], ['Daily high', f(q.high)],
    ['Daily range', `${Math.round((q.high - q.low) / 10 ** -q.digits)} points`], ['Daily change', `${signed(q.change_pts)} points (${signed(q.change_pct, 2)}%)`],
    ['Last quote', stamp(q.t_ms, t.cfg.tz)],
  ] : []
  return (
    <Page title={symbol} sub="Market statistics" onBack={onBack} testId="statistics">
      {!q ? <Empty icon={<EQuotes />} text="No quotes yet" /> : <div className="art" style={{ padding: '6px 0' }}>{rows.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
          <span style={{ color: 'var(--dim)' }}>{k}</span><span style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{v}</span></div>))}</div>}
    </Page>
  )
}
