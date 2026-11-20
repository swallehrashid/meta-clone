import { useMemo, useRef, useState } from 'react'
import { lots } from './calc'
import { ChartView, type ChartColors, type Level } from './ChartView'
import { useT } from './ctx'
import { priceParts, vol } from './format'
import { ICrosshair, IChevDown, IChevUp, IGear, IIndicators, IMenu, IMove, IOneClick, IOrderRing } from './icons'
import { OrderPanel, sideOf, typeLabel, type Draft } from './OrderPanel'
import { ALL_TFS, type Prefs, type TfName } from './prefs'
import { Dialog, IconBtn, Menu, type MenuItem } from './ui'

export function chartColors(prefs: Prefs): ChartColors {
  const th = prefs.theme
  const bg = th === 'light' ? '#ffffff' : th === 'dark' ? '#1d1e22' : '#000000'
  const grid = th === 'light' ? '#e2e2e2' : th === 'dark' ? '#3b3e45' : '#363636'
  const scale = th === 'light' ? '#222222' : '#d8d8d8'
  let up = '#26a69a', down = th === 'light' ? '#111111' : '#ef5350'
  if (prefs.colorScheme === 'green') { up = '#2fd36b'; down = '#e5534b' }
  if (prefs.colorScheme === 'bw') { up = th === 'light' ? '#ffffff' : '#e0e0e0'; down = th === 'light' ? '#000000' : '#6e6e6e' }
  return { bg, text: scale, grid, scale, up, down, ask: '#ef5350', bid: '#26a69a' }
}

const PRESETS = [0.01, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]

export function ChartScreen({ draft, setDraft }: { draft: Draft; setDraft: (d: Draft) => void }) {
  const t = useT()
  const p = t.prefs
  const [tfBar, setTfBar] = useState(false)
  const [cross, setCross] = useState(false)
  const [vdd, setVdd] = useState<null | 'up' | 'down'>(null)
  const [menu, setMenu] = useState<{ items: MenuItem[]; at: { top?: number; left?: number; right?: number } } | null>(null)
  const [dlg, setDlg] = useState<null | { kind: 'oneclick' } | { kind: 'volume' } | { kind: 'confirm'; text: string; run: () => void }>(null)
  const [volText, setVolText] = useState('')
  const sym = t.chartSym
  const d = t.store.data.get(sym)
  const tick = d?.lastTick
  const digits = t.digitsOf(sym)
  const volume = p.volume ?? t.cfg.volume
  const showPanel = p.oneClick && p.oneClickPanel
  const prev = useRef({ bid: 0, ask: 0, bidUp: false, askUp: true })
  if (tick && tick.bid !== prev.current.bid) prev.current = { ...prev.current, bidUp: tick.bid > prev.current.bid, bid: tick.bid }
  if (tick && tick.ask !== prev.current.ask) prev.current = { ...prev.current, askUp: tick.ask > prev.current.ask, ask: tick.ask }
  const colors = useMemo(() => chartColors(p), [p.theme, p.colorScheme])     // eslint-disable-line react-hooks/exhaustive-deps

  const confirm = (text: string, run: () => void) => { if (p.confirmOnChart) run(); else setDlg({ kind: 'confirm', text, run }) }
  const levels = useMemo<Level[]>(() => {
    const out: Level[] = []
    const fx = (x: number) => x.toFixed(digits)
    if (p.showTradeLevels && t.trade) {
      for (const x of t.trade.positions.filter((x) => x.symbol === sym)) {
        out.push({ key: `p${x.id}`, price: x.open_price, color: x.side === 'buy' ? '#3d8cf4' : '#ef5350', label: `${x.side.toUpperCase()} ${vol(x.volume)}`, dashed: true })
        const editing = t.panel?.kind === 'modify' && t.panel.positionId === x.id
        if (x.sl !== null && !editing) out.push({ key: `p${x.id}sl`, price: x.sl, color: '#ef5350', label: 'SL', dashed: true, draggable: true,
          onDrop: (v) => confirm(`Modify position #${x.id}: stop loss ${fx(v)}?`, () => { void t.modifyPos(x.id, v, x.tp) }) })
        if (x.tp !== null && !editing) out.push({ key: `p${x.id}tp`, price: x.tp, color: '#36b24a', label: 'TP', dashed: true, draggable: true,
          onDrop: (v) => confirm(`Modify position #${x.id}: take profit ${fx(v)}?`, () => { void t.modifyPos(x.id, x.sl, v) }) })
      }
      for (const o of t.trade.orders.filter((o) => o.symbol === sym && o.kind === 'open' && o.price !== null)) {
        const name = `${o.side} ${o.type.replace('_', ' ')}`.toUpperCase()
        out.push({ key: `o${o.id}`, price: o.price!, color: o.side === 'buy' ? '#3d8cf4' : '#ef5350', label: `${name} ${vol(o.volume)}`, draggable: true,
          onDrop: (v) => confirm(`Move order #${o.id} to ${fx(v)}?`, () => { void t.modifyOrd(o.id, { price: v }) }) })
        if (o.type === 'stop_limit' && o.stoplimit) out.push({ key: `o${o.id}sl2`, price: o.stoplimit, color: '#e0e0e0', label: 'STOP LIMIT PRICE', dashed: true })
      }
    }
    // the order being prepared in the panel
    const pm = t.panel
    if (pm && draft.symbol === sym && (pm.kind === 'new' || pm.kind === 'modify' || pm.kind === 'order')) {
      const s = sideOf(draft.otype)
      if (pm.kind === 'new' && draft.otype !== 'market' && draft.price !== null)
        out.push({ key: 'dprice', price: draft.price, color: s === 'buy' ? '#3d8cf4' : '#ef5350', label: `${typeLabel(draft.otype).toUpperCase()} ${vol(draft.volume)}`, draggable: true,
                   onDrag: (v) => setDraft({ ...draft, price: v }) })
      if (pm.kind === 'new' && draft.otype.endsWith('stop_limit') && draft.stoplimit !== null)
        out.push({ key: 'dstoplimit', price: draft.stoplimit, color: '#e0e0e0', label: 'STOP LIMIT PRICE', draggable: true, onDrag: (v) => setDraft({ ...draft, stoplimit: v }) })
      if (draft.sl !== null) out.push({ key: 'dsl', price: draft.sl, color: '#ef5350', label: 'SL', dashed: true, draggable: true, onDrag: (v) => setDraft({ ...draft, sl: v }) })
      if (draft.tp !== null) out.push({ key: 'dtp', price: draft.tp, color: '#36b24a', label: 'TP', dashed: true, draggable: true, onDrag: (v) => setDraft({ ...draft, tp: v }) })
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t.trade, sym, p.showTradeLevels, t.panel, draft, digits, p.confirmOnChart])

  const oneClick = (side: 'buy' | 'sell') => { void t.place({ symbol: sym, side, type: 'market', volume }) }
  const setVol = (v: number) => t.setPrefs({ volume: lots(Math.max(0.01, Math.min(50, v))) })
  const toggleOneClick = () => {
    if (!p.oneClick) { setDlg({ kind: 'oneclick' }); return }
    t.setPrefs({ oneClickPanel: !p.oneClickPanel })
  }
  const toggleOrder = () => { if (t.panel) t.setPanel(null); else t.setPanel({ kind: 'new', symbol: sym }) }
  const symbolMenu = () => setMenu({ at: { top: 60, left: 8 }, items: t.symbols.map((s) => ({ label: s, sub: t.describe(s), checked: s === sym, onClick: () => t.setChartSym(s) })) })
  const moreMenu = () => setMenu({ at: { right: 12, top: 170 }, items: [
    { label: 'Indicators', onClick: () => t.push({ id: 'indicators' }), testId: 'more-indicators' },
    { label: 'Timeframes', onClick: () => setTfBar(true) },
    { label: 'Chart settings', onClick: () => t.push({ id: 'chartSettings' }), testId: 'more-settings' },
    { label: cross ? 'Hide crosshair' : 'Crosshair', onClick: () => setCross((v) => !v) },
    { label: 'New order', onClick: () => t.setPanel({ kind: 'new', symbol: sym, expanded: true }) },
    { label: showPanel ? 'Hide One Click Trading' : 'One Click Trading', onClick: toggleOneClick },
    { label: p.showTradeLevels ? 'Hide trade levels' : 'Show trade levels', onClick: () => t.setPrefs({ showTradeLevels: !p.showTradeLevels }) },
  ] })

  const Box = ({ side }: { side: 'sell' | 'buy' }) => {
    const v = side === 'sell' ? tick?.bid : tick?.ask
    const up = side === 'sell' ? prev.current.bidUp : prev.current.askUp
    const pp = v !== undefined ? priceParts(v, digits) : null
    return (
      <button className={`ocb ${up ? 'up' : 'dn'}`} disabled={!tick} onClick={() => oneClick(side)} data-testid={`oc-${side}`}>
        <span className="t">{side.toUpperCase()}</span>
        {pp && <span className="px"><span className="pre">{pp.pre}</span><span className="big">{pp.big}</span><span className="sup">{pp.sup}</span></span>}
      </button>
    )
  }
  const lower = PRESETS.filter((x) => x < volume - 1e-9), higher = PRESETS.filter((x) => x > volume + 1e-9)

  return (
    <div className="main" data-testid="chart-screen">
      {tfBar ? (
        <div className="tfbar" data-testid="tf-bar">
          {ALL_TFS.map((x) => <button key={x} className={x === t.tf ? 'on' : ''} onClick={() => { t.setTf(x as TfName); setTfBar(false) }} data-testid={`tf-${x}`}>{x}</button>)}
          <IconBtn onClick={() => { setTfBar(false); t.push({ id: 'chartSettings' }) }} label="Chart settings" testId="tf-gear"><IGear /></IconBtn>
        </div>
      ) : (
        <div className="ctb">
          <IconBtn onClick={t.openDrawer} label="Menu" testId="menu"><IMenu /></IconBtn>
          <span className="sp" />
          <IconBtn onClick={() => setCross((v) => !v)} label="Crosshair" testId="crosshair">{cross ? <IMove /> : <ICrosshair />}</IconBtn>
          <IconBtn onClick={() => t.push({ id: 'indicators' })} label="Indicators" testId="indicators"><IIndicators /></IconBtn>
          <button className="tfl" onClick={() => setTfBar(true)} data-testid="tf">{t.tf}</button>
          <IconBtn onClick={toggleOrder} label="New order" testId="order-ring"><IOrderRing /></IconBtn>
          <IconBtn onClick={toggleOneClick} label="One Click Trading" testId="oneclick-toggle"><IOneClick /></IconBtn>
        </div>
      )}
      {showPanel && (
        <div className="ocp" data-testid="oneclick">
          <Box side="sell" />
          <div className="ocv">
            <button className={vdd === 'down' ? 'on' : ''} onClick={() => setVdd(vdd === 'down' ? null : 'down')} aria-label="Less" data-testid="vol-down"><IChevDown /></button>
            <button className="v" onClick={() => { setVolText(vol(volume)); setDlg({ kind: 'volume' }) }} data-testid="oc-volume">{vol(volume)}</button>
            <button className={vdd === 'up' ? 'on' : ''} onClick={() => setVdd(vdd === 'up' ? null : 'up')} aria-label="More" data-testid="vol-up"><IChevUp /></button>
          </div>
          <Box side="buy" />
          {vdd && (
            <div className="vdd" style={{ top: 41 }} data-testid="vol-dropdown">
              <div className="inc">{[0.01, 0.1, 0.5].map((x) => <button key={x} onClick={() => { setVol(volume + (vdd === 'up' ? x : -x)); setVdd(null) }}>{vdd === 'up' ? '+' : '-'}{x}</button>)}</div>
              <div className="grid" style={{ gridTemplateRows: `repeat(${Math.min(5, Math.max(1, Math.ceil((vdd === 'up' ? higher : lower).length / 2)))}, 33px)` }}>
                {(vdd === 'up' ? higher : lower).map((x) => <button key={x} onClick={() => { setVol(x); setVdd(null) }}>{vol(x)}</button>)}
              </div>
            </div>
          )}
        </div>
      )}
      <ChartView symbol={sym} description={t.describe(sym)} store={t.store} tz={t.cfg.tz} delayS={t.cfg.delay_s} tf={t.tf} kind={p.chartType} colors={colors}
                 grid={p.showGrid} showAsk={p.showAsk ?? t.cfg.show_ask_line} volumes={p.showVolumes} ohlc={p.showOHLC} separators={p.showSeparators}
                 crosshair={cross} indicators={(p.indicators[sym] ?? []).filter((x) => !x.params.__hidden)} levels={levels}
                 onPickSymbol={symbolMenu} onPickTf={() => setTfBar(true)} onMore={moreMenu} />
      {t.panel && <OrderPanel key={`${t.panel.kind}-${'positionId' in t.panel ? t.panel.positionId : 'orderId' in t.panel ? t.panel.orderId : ''}`}
                              mode={t.panel} draft={draft} setDraft={setDraft} onClose={() => t.setPanel(null)} />}

      {menu && <Menu items={menu.items} at={menu.at} onClose={() => setMenu(null)} wide />}
      {dlg?.kind === 'oneclick' && (
        <Dialog title="One Click Trading" onClose={() => setDlg(null)} testId="oneclick-dialog" actions={[
          { label: 'Cancel', onClick: () => setDlg(null) },
          { label: 'I accept', primary: true, testId: 'oneclick-accept', onClick: () => { t.setPrefs({ oneClick: true, oneClickPanel: true }); setDlg(null) } }]}>
          One Click Trading lets you trade with a single tap: SELL and BUY are sent immediately, without any further confirmation.
          You accept that orders placed this way are executed at once, and that you are responsible for them.
        </Dialog>
      )}
      {dlg?.kind === 'volume' && (
        <Dialog title="Volume" onClose={() => setDlg(null)} actions={[{ label: 'Cancel', onClick: () => setDlg(null) },
          { label: 'OK', primary: true, testId: 'vol-ok', onClick: () => { const v = Number(volText.replace(',', '.')); if (isFinite(v) && v > 0) setVol(v); setDlg(null) } }]}>
          <input autoFocus inputMode="decimal" value={volText} onChange={(e) => setVolText(e.target.value)} data-testid="vol-input" />
        </Dialog>
      )}
      {dlg?.kind === 'confirm' && (
        <Dialog title="Confirm" onClose={() => setDlg(null)} actions={[{ label: 'Cancel', onClick: () => setDlg(null) }, { label: 'OK', primary: true, testId: 'confirm-ok', onClick: () => { dlg.run(); setDlg(null) } }]}>
          {dlg.text}
        </Dialog>
      )}
    </div>
  )
}
