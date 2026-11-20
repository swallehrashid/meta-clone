import { useEffect, useMemo, useRef, useState } from 'react'
import { lots, marginUsd, maxLots, profitUsd } from './calc'
import { useT, type PanelMode } from './ctx'
import { money, stamp, vol } from './format'
import { IArrowRight, IChevDown, IChevUp, IClose, IUpDown } from './icons'
import { Dialog, Menu, type MenuItem } from './ui'

export type OType = 'market' | 'buy_limit' | 'sell_limit' | 'buy_stop' | 'sell_stop' | 'buy_stop_limit' | 'sell_stop_limit'
export const OTYPES: { id: OType; label: string }[] = [
  { id: 'market', label: 'Market Execution' }, { id: 'buy_limit', label: 'Buy Limit' }, { id: 'sell_limit', label: 'Sell Limit' },
  { id: 'buy_stop', label: 'Buy Stop' }, { id: 'sell_stop', label: 'Sell Stop' }, { id: 'buy_stop_limit', label: 'Buy Stop Limit' },
  { id: 'sell_stop_limit', label: 'Sell Stop Limit' },
]
export const sideOf = (t: OType): 'buy' | 'sell' | null => (t === 'market' ? null : t.startsWith('buy') ? 'buy' : 'sell')
export const engineType = (t: OType) => (t === 'market' ? 'market' : t.endsWith('stop_limit') ? 'stop_limit' : t.endsWith('limit') ? 'limit' : 'stop') as 'market' | 'limit' | 'stop' | 'stop_limit'
export const typeLabel = (t: OType) => OTYPES.find((o) => o.id === t)!.label

export interface Draft {
  symbol: string
  otype: OType
  volume: number
  price: number | null
  stoplimit: number | null
  sl: number | null
  tp: number | null
  stops: boolean
  expiration: 'gtc' | 'today' | 'specified'
  expireMs: number | null
  fill: 'Fill or Kill' | 'Immediate or Cancel' | 'Return'
  comment: string | null
  units: 'Lots' | 'Units'
}

export function newDraft(symbol: string, volume: number, otype: OType = 'market'): Draft {
  return { symbol, otype, volume, price: null, stoplimit: null, sl: null, tp: null, stops: false, expiration: 'gtc', expireMs: null,
           fill: 'Fill or Kill', comment: null, units: 'Lots' }
}

/** A valid default price for a pending type: a few points away from the market on the correct side. */
export function defaultPrices(t: OType, bid: number, ask: number, digits: number): { price: number | null; stoplimit: number | null } {
  const pt = 10 ** -digits, r = (x: number) => Number(x.toFixed(digits))
  switch (t) {
    case 'buy_limit': return { price: r(ask - 50 * pt), stoplimit: null }
    case 'sell_limit': return { price: r(bid + 50 * pt), stoplimit: null }
    case 'buy_stop': return { price: r(ask + 50 * pt), stoplimit: null }
    case 'sell_stop': return { price: r(bid - 50 * pt), stoplimit: null }
    case 'buy_stop_limit': return { price: r(ask + 50 * pt), stoplimit: r(ask + 20 * pt) }
    case 'sell_stop_limit': return { price: r(bid - 50 * pt), stoplimit: r(bid - 20 * pt) }
    default: return { price: null, stoplimit: null }
  }
}

/** SL / TP a sensible distance from the entry when "Add Stop Levels" is tapped. */
export function defaultStops(side: 'buy' | 'sell', entry: number, digits: number): { sl: number; tp: number } {
  const pt = 10 ** -digits, d = 100 * pt, r = (x: number) => Number(x.toFixed(digits))
  return side === 'buy' ? { sl: r(entry - d), tp: r(entry + d) } : { sl: r(entry + d), tp: r(entry - d) }
}

export function OrderPanel({ mode, draft, setDraft, full, onClose }: { mode: PanelMode; draft: Draft; setDraft: (d: Draft) => void; full?: boolean; onClose: () => void }) {
  const t = useT()
  const [exp, setExp] = useState<boolean>(full || (mode.kind === 'new' ? !!mode.expanded : true))
  const [menu, setMenu] = useState<{ items: MenuItem[]; at: { top?: number; left?: number; right?: number; bottom?: number } } | null>(null)
  const [dlg, setDlg] = useState<null | 'expire'>(null)
  const [busy, setBusy] = useState(false)
  const q = t.quotes.get(draft.symbol)
  const sd = t.store.data.get(draft.symbol)
  const tick = sd?.lastTick
  const bid = tick?.bid ?? q?.bid ?? 0, ask = tick?.ask ?? q?.ask ?? 0
  const digits = t.digitsOf(draft.symbol)
  const pt = 10 ** -digits
  const fx = (x: number | null | undefined) => (x === null || x === undefined ? '' : x.toFixed(digits))
  const prev = useRef({ bid, ask, bidUp: true, askUp: true })
  if (bid !== prev.current.bid) prev.current = { ...prev.current, bidUp: bid > prev.current.bid, bid }
  if (ask !== prev.current.ask) prev.current = { ...prev.current, askUp: ask > prev.current.ask, ask }
  const acct = t.trade?.account
  const qmap = useMemo(() => {
    const m = new Map<string, { bid: number; ask: number }>()
    for (const [k, v] of t.quotes) m.set(k, v)
    if (tick) m.set(draft.symbol, tick)
    return m
  }, [t.quotes, tick, draft.symbol])

  const set = (patch: Partial<Draft>) => setDraft({ ...draft, ...patch })
  const side = sideOf(draft.otype)
  const pos = mode.kind === 'close' || mode.kind === 'modify' ? t.trade?.positions.find((x) => x.id === mode.positionId) : undefined
  const ord = mode.kind === 'order' ? t.trade?.orders.find((x) => x.id === mode.orderId) : undefined
  useEffect(() => { if ((mode.kind === 'close' || mode.kind === 'modify') && !pos) onClose(); if (mode.kind === 'order' && !ord) onClose() })   // it went away

  // choose a type: give pending types a valid price
  const pickType = (ot: OType) => {
    const d = defaultPrices(ot, bid, ask, digits)
    const keep = draft.price !== null && ot !== 'market' && ot.endsWith('stop_limit') === draft.otype.endsWith('stop_limit') && sideOf(ot) === sideOf(draft.otype) && engineType(ot) === engineType(draft.otype)
    const next: Partial<Draft> = { otype: ot, price: keep ? draft.price : d.price, stoplimit: keep ? draft.stoplimit : d.stoplimit }
    if (draft.stops) {
      const s = sideOf(ot) ?? 'buy', entry = (ot.endsWith('stop_limit') ? next.stoplimit : next.price) ?? (s === 'buy' ? ask : bid)
      Object.assign(next, defaultStops(s, entry, digits))
    }
    set(next)
  }
  const step = (k: 'price' | 'stoplimit' | 'sl' | 'tp', dir: 1 | -1) => {
    const base = draft[k] ?? (k === 'sl' || k === 'tp' ? (side === 'sell' ? bid : ask) : side === 'sell' ? bid : ask)
    set({ [k]: Number((base + dir * pt).toFixed(digits)) } as Partial<Draft>)
  }
  const stepVol = (dir: 1 | -1) => set({ volume: lots(Math.max(0.01, Math.min(50, draft.volume + dir * 0.01))) })
  const max = acct ? maxLots(draft.symbol, acct.free_margin, acct.leverage, qmap) : 50
  const margin = acct ? marginUsd(draft.symbol, draft.volume, acct.leverage, qmap) : null
  const addStops = () => {
    if (draft.stops) { set({ stops: false, sl: null, tp: null }); return }
    const s = side ?? 'buy', entry = (draft.otype.endsWith('stop_limit') ? draft.stoplimit : draft.price) ?? (s === 'buy' ? ask : bid)
    set({ stops: true, ...defaultStops(s, entry, digits) })
  }
  const toggleOne = (k: 'sl' | 'tp') => {
    if (draft[k] !== null) { const n = { ...draft, [k]: null }; n.stops = n.sl !== null || n.tp !== null; setDraft(n); return }
    const s = side ?? 'buy', entry = (draft.otype.endsWith('stop_limit') ? draft.stoplimit : draft.price) ?? (s === 'buy' ? ask : bid)
    setDraft({ ...draft, stops: true, [k]: defaultStops(s, entry, digits)[k] })
  }

  const submit = async (s?: 'buy' | 'sell') => {
    if (busy) return
    setBusy(true)
    try {
      if (mode.kind === 'new') {
        const sideNow = s ?? side ?? 'buy'
        const ok = await t.place({ symbol: draft.symbol, side: sideNow, type: engineType(draft.otype), volume: draft.volume,
          price: draft.otype === 'market' ? null : draft.price, stoplimit: draft.otype.endsWith('stop_limit') ? draft.stoplimit : null,
          sl: draft.stops ? draft.sl : null, tp: draft.stops ? draft.tp : null, comment: draft.comment ?? '',
          expiration: draft.otype === 'market' ? 'gtc' : draft.expiration, expire_ms: draft.expiration === 'specified' ? draft.expireMs : null })
        if (ok && !full) setExp(false)
        if (ok && full) onClose()
      } else if (mode.kind === 'close' && pos) {
        if (await t.closePos(pos.id, draft.volume >= pos.volume - 1e-9 ? undefined : draft.volume)) onClose()
      } else if (mode.kind === 'modify' && pos) {
        if (await t.modifyPos(pos.id, draft.sl, draft.tp)) onClose()
      } else if (mode.kind === 'order' && ord) {
        const change: { price?: number; sl: number | null; tp: number | null; stoplimit?: number | null } = { sl: draft.sl, tp: draft.tp }
        if (draft.price !== null) change.price = draft.price
        if (ord.type === 'stop_limit') change.stoplimit = draft.stoplimit
        if (await t.modifyOrd(ord.id, change)) onClose()
      }
    } finally { setBusy(false) }
  }

  // ---- grab handle: tap toggles, drag up expands, drag down collapses / closes
  const grab = useRef<{ y: number; moved: boolean } | null>(null)
  const onGrabDown = (e: React.PointerEvent) => { grab.current = { y: e.clientY, moved: false }; (e.target as HTMLElement).setPointerCapture?.(e.pointerId) }
  const onGrabMove = (e: React.PointerEvent) => {
    const g = grab.current
    if (!g || g.moved) return
    const dy = e.clientY - g.y
    if (dy < -24) { g.moved = true; setExp(true) }
    else if (dy > 24) { g.moved = true; if (exp && !full) setExp(false); else onClose() }
  }
  const onGrabUp = () => { const g = grab.current; grab.current = null; if (g && !g.moved) { if (full) onClose(); else setExp((v) => !v) } }

  const symbolMenu = (e: React.MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setMenu({ at: { top: Math.max(8, r.bottom - 300), left: 16 }, items: t.symbols.map((s) => ({ label: s, sub: t.describe(s), checked: s === draft.symbol, onClick: () => {
      const qq = t.quotes.get(s)
      const d = qq ? defaultPrices(draft.otype, qq.bid, qq.ask, qq.digits) : { price: null, stoplimit: null }
      setDraft({ ...draft, symbol: s, ...d, sl: null, tp: null, stops: false })
    } })) })
  }
  const menuAt = (e: React.MouseEvent, items: MenuItem[]) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect(), host = (e.currentTarget as HTMLElement).closest('.mt5')!.getBoundingClientRect()
    setMenu({ at: { top: Math.min(r.bottom - host.top + 4, host.height - 60 - items.length * 49), left: r.left - host.left }, items })
  }

  const title = mode.kind === 'new' ? null
    : mode.kind === 'order' && ord ? `#${ord.id} ${ord.side} ${ord.type.replace('_', ' ')} ${vol(ord.volume)}`
      : pos ? `#${pos.id} ${pos.side} ${vol(pos.volume)}` : ''
  const isPending = draft.otype !== 'market'
  const placeLabel = `Place ${typeLabel(draft.otype)}`
  const closeProfit = pos && pos.price !== null ? profitUsd(pos.symbol, pos.side, draft.volume, pos.open_price, pos.price, qmap) : null

  return (
    <div className={`op${full ? ' full' : ''}${!exp ? ' col' : ''}`} data-testid="order-panel" data-expanded={exp ? '1' : '0'}>
      <div className="grab" onPointerDown={onGrabDown} onPointerMove={onGrabMove} onPointerUp={onGrabUp} data-testid="op-grab" />
      <div className="body">
        <div className="ophd">
          <button className="opsym" onClick={mode.kind === 'new' ? symbolMenu : undefined} data-testid="op-symbol">{draft.symbol}{mode.kind === 'new' && <IUpDown size={20} />}</button>
          {exp || mode.kind !== 'new' ? (
            <div className="opq" data-testid="op-quote">
              <span className={prev.current.bidUp ? 'up' : 'dn'}>{fx(bid)}</span><span className={prev.current.askUp ? 'up' : 'dn'}>{fx(ask)}</span>
            </div>
          ) : (
            <div className="circ">
              <button className={`sl${draft.sl !== null ? ' on' : ''}`} onClick={() => toggleOne('sl')} data-testid="op-sl">SL</button>
              <button className={`tp${draft.tp !== null ? ' on' : ''}`} onClick={() => toggleOne('tp')} data-testid="op-tp">TP</button>
              <button className="go" onClick={() => (draft.otype === 'market' ? setExp(true) : submit())} aria-label="Place" data-testid="op-go"><IArrowRight size={28} /></button>
            </div>
          )}
          {full && <button className="ib" onClick={onClose} aria-label="Close"><IClose /></button>}
        </div>
        <div className="opdesc">{title ?? t.describe(draft.symbol)}</div>

        {mode.kind === 'new' && (
          <div className="optabs" data-testid="op-tabs">
            {OTYPES.map((o) => (
              <button key={o.id} className={`optab${draft.otype === o.id ? ` on ${o.id === 'market' ? 'mkt' : sideOf(o.id)}` : ''}`} onClick={() => pickType(o.id)}
                      ref={(el) => { if (el && draft.otype === o.id) el.scrollIntoView({ block: 'nearest', inline: 'center' }) }} data-testid={`op-type-${o.id}`}>{o.label}</button>
            ))}
          </div>
        )}

        {(mode.kind === 'new' || mode.kind === 'close') && (
          <>
            <div className="fld">
              <label>Volume</label>
              <input inputMode="decimal" value={draft.units === 'Lots' ? vol(draft.volume) : String(Math.round(draft.volume * 100000))} data-testid="op-volume"
                     onChange={(e) => { const v = Number(e.target.value.replace(',', '.')); if (isFinite(v) && v > 0) set({ volume: lots(draft.units === 'Lots' ? v : v / 100000) }) }} />
              <span className="stp"><button onClick={() => stepVol(1)} aria-label="More"><IChevUp /></button><button onClick={() => stepVol(-1)} aria-label="Less"><IChevDown /></button></span>
              <button className="unit" onClick={(e) => menuAt(e, ['Lots', 'Units'].map((u) => ({ label: u, checked: draft.units === u, onClick: () => set({ units: u as Draft['units'] }) })))}
                      data-testid="op-units">{draft.units} <IUpDown size={16} /></button>
            </div>
            {mode.kind === 'new' && <Slider value={draft.volume} max={max} onChange={(v) => set({ volume: v })} />}
            {mode.kind === 'new' && (
              <div className="mrg">
                <span><u onClick={() => t.toast('Margin: the money set aside while this trade is open (volume x contract x price / leverage)')}>Margin</u> ≈ {margin === null ? '–' : money(margin)} USD</span>
                <span>Free: {acct ? money(acct.free_margin) : '–'} USD</span>
              </div>
            )}
          </>
        )}

        {mode.kind === 'close' && pos && (
          <div className="info" data-testid="op-close-info">
            <span>Open price</span><span>{fx(pos.open_price)}</span>
            <span>Current price</span><span>{fx(pos.price)}</span>
            <span>Opened</span><span>{stamp(pos.open_ms, t.cfg.tz)}</span>
            <span>Profit</span><span style={{ color: (closeProfit ?? 0) < 0 ? 'var(--red)' : 'var(--blue)' }}>{money(closeProfit)}</span>
          </div>
        )}

        {((mode.kind === 'new' && isPending) || (mode.kind === 'order' && ord)) && (
          <PriceField label="Price" value={draft.price} fx={fx} onChange={(v) => set({ price: v })} onStep={(d) => step('price', d)} testId="op-price" />
        )}
        {((mode.kind === 'new' && draft.otype.endsWith('stop_limit')) || (mode.kind === 'order' && ord?.type === 'stop_limit')) && (
          <PriceField label="Stop Limit Price" value={draft.stoplimit} fx={fx} onChange={(v) => set({ stoplimit: v })} onStep={(d) => step('stoplimit', d)} testId="op-stoplimit" />
        )}

        {(mode.kind === 'modify' || mode.kind === 'order' || draft.stops) && mode.kind !== 'close' && (
          <>
            <PriceField label="Stop Loss" value={draft.sl} fx={fx} onChange={(v) => set({ sl: v })} onStep={(d) => step('sl', d)} onClear={() => set({ sl: null })} testId="op-sl-field" />
            <PriceField label="Take Profit" value={draft.tp} fx={fx} onChange={(v) => set({ tp: v })} onStep={(d) => step('tp', d)} onClear={() => set({ tp: null })} testId="op-tp-field" />
          </>
        )}
        {mode.kind === 'new' && <button className="addsl" onClick={addStops} data-testid="op-addstops">{draft.stops ? 'Remove Stop Levels' : 'Add Stop Levels'}</button>}

        {mode.kind === 'new' && !isPending && (
          <div className="opl">Fill policy: <button onClick={(e) => menuAt(e, (['Fill or Kill', 'Immediate or Cancel', 'Return'] as const).map((f) => ({ label: f, checked: draft.fill === f, onClick: () => set({ fill: f }) })))} data-testid="op-fill">{draft.fill} <IUpDown size={14} /></button></div>
        )}
        {mode.kind === 'new' && isPending && (
          <div className="opl">Expiration: <button data-testid="op-expiration" onClick={(e) => menuAt(e, [
            { label: 'GTC', sub: 'Good till cancelled', checked: draft.expiration === 'gtc', onClick: () => set({ expiration: 'gtc', expireMs: null }) },
            { label: 'Today', sub: 'Until the end of the trading day', checked: draft.expiration === 'today', onClick: () => set({ expiration: 'today', expireMs: null }) },
            { label: 'Specified', sub: 'Until a date and time you choose', checked: draft.expiration === 'specified', onClick: () => setDlg('expire') },
          ])}>{draft.expiration === 'gtc' ? 'GTC' : draft.expiration === 'today' ? 'Today' : draft.expireMs ? stamp(draft.expireMs, t.cfg.tz).slice(0, 16) : 'Specified'} <IUpDown size={14} /></button></div>
        )}
        {mode.kind === 'new' && (
          <div className="opl">Comment: {draft.comment === null
            ? <button onClick={() => set({ comment: '' })} data-testid="op-comment">Add comment</button>
            : <input autoFocus maxLength={31} value={draft.comment} onChange={(e) => set({ comment: e.target.value })} data-testid="op-comment-input" />}</div>
        )}

        <div className="btns">
          {mode.kind === 'new' && !isPending && <>
            <button className="bs" disabled={busy || !tick} onClick={() => submit('sell')} data-testid="op-sell">Sell</button>
            <button className="bb" disabled={busy || !tick} onClick={() => submit('buy')} data-testid="op-buy">Buy</button>
          </>}
          {mode.kind === 'new' && isPending && <button className={side === 'sell' ? 'bs' : 'bb'} disabled={busy} onClick={() => submit()} data-testid="op-place">{placeLabel}</button>}
          {mode.kind === 'close' && pos && <button className="by" disabled={busy} onClick={() => submit()} data-testid="op-close">
            Close #{pos.id} {pos.side} {vol(draft.volume)} {pos.symbol} at {fx(pos.price)} with {closeProfit !== null && closeProfit < 0 ? 'loss' : 'profit'} {money(closeProfit)}</button>}
          {mode.kind === 'modify' && pos && <button className="bb" disabled={busy} onClick={() => submit()} data-testid="op-modify">Modify #{pos.id} {pos.side} {vol(pos.volume)} {pos.symbol}</button>}
          {mode.kind === 'order' && ord && <>
            <button className="bs" disabled={busy} onClick={async () => { if (await t.cancelOrd(ord.id)) onClose() }} data-testid="op-delete">Delete</button>
            <button className="bb" disabled={busy} onClick={() => submit()} data-testid="op-modify-order">Modify</button>
          </>}
        </div>

        {mode.kind === 'new' && isPending && draft.price !== null && (
          <Ladder price={draft.price} market={side === 'buy' ? ask : bid} digits={digits} onChange={(v) => set({ price: v })} />
        )}
      </div>

      {menu && <Menu items={menu.items} at={menu.at} onClose={() => setMenu(null)} wide={menu.items.length > 3} />}
      {dlg === 'expire' && <ExpireDialog tz={t.cfg.tz} initial={draft.expireMs ?? Date.now() - t.cfg.delay_s * 1000 + 3600_000}
        onCancel={() => setDlg(null)} onSet={(ms) => { set({ expiration: 'specified', expireMs: ms }); setDlg(null) }} />}
    </div>
  )
}

function PriceField(p: { label: string; value: number | null; fx: (x: number | null) => string; onChange: (v: number | null) => void; onStep: (d: 1 | -1) => void; onClear?: () => void; testId: string }) {
  const [text, setText] = useState(p.fx(p.value))
  const focused = useRef(false)
  useEffect(() => { if (!focused.current) setText(p.fx(p.value)) }, [p.value])   // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="fld">
      <label>{p.label}</label>
      <input inputMode="decimal" value={text} placeholder="not set" data-testid={p.testId}
             onFocus={() => { focused.current = true }} onBlur={() => { focused.current = false; setText(p.fx(p.value)) }}
             onChange={(e) => { setText(e.target.value); const v = Number(e.target.value.replace(',', '.')); if (e.target.value.trim() === '') p.onChange(null); else if (isFinite(v) && v > 0) p.onChange(v) }} />
      {p.onClear && p.value !== null && <button className="clr" onClick={p.onClear} aria-label="Clear"><IClose size={16} /></button>}
      <span className="stp"><button onClick={() => p.onStep(1)} aria-label="Up"><IChevUp /></button><button onClick={() => p.onStep(-1)} aria-label="Down"><IChevDown /></button></span>
    </div>
  )
}

function Slider({ value, max, onChange }: { value: number; max: number; onChange: (v: number) => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const frac = Math.max(0, Math.min(1, (value - 0.01) / Math.max(0.01, max - 0.01)))
  const at = (x: number) => { const r = ref.current!.getBoundingClientRect(); const f = Math.max(0, Math.min(1, (x - r.left) / r.width)); onChange(lots(0.01 + f * (max - 0.01))) }
  return (
    <div className="slider" ref={ref} data-testid="op-slider"
         onPointerDown={(e) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); at(e.clientX) }}
         onPointerMove={(e) => { if (e.buttons) at(e.clientX) }}>
      <div className="trk" /><div className="fil" style={{ width: `${frac * 100}%` }} /><div className="kn" style={{ left: `${frac * 100}%` }} />
    </div>
  )
}

/** The price ladder under a pending order: drag the knob to move the order price. */
function Ladder({ price, market, digits, onChange }: { price: number; market: number; digits: number; onChange: (v: number) => void }) {
  const pt = 10 ** -digits
  const [center, setCenter] = useState(price)
  const H = 230, pxPerPt = 1.6
  const y = (v: number) => H / 2 - (v - center) / pt * pxPerPt
  const start = useRef<{ y: number; p: number } | null>(null)
  const levels: number[] = []
  const stepPts = 20
  const top = center + (H / 2 / pxPerPt) * pt
  for (let v = Math.floor(top / (stepPts * pt)) * stepPts * pt; y(v) < H; v -= stepPts * pt) levels.push(v)
  const my = y(market)
  return (
    <div className="ladder" data-testid="op-ladder">
      {levels.map((v) => <div key={v.toFixed(digits)} className="lv" style={{ top: y(v) }}><span>{v.toFixed(digits)}</span></div>)}
      {my > 0 && my < H && <div className="mkt" style={{ top: my }}><span>{market.toFixed(digits)}</span></div>}
      <div className="cur" style={{ top: y(price) }}>
        <span className="lab">Price</span>
        <span className="knob" data-testid="op-ladder-knob"
              onPointerDown={(e) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); start.current = { y: e.clientY, p: price } }}
              onPointerMove={(e) => { const s = start.current; if (!s) return; onChange(Number((s.p - ((e.clientY - s.y) / pxPerPt) * pt).toFixed(digits))) }}
              onPointerUp={() => { start.current = null; setCenter(price) }} />
        <span className="tagp">{price.toFixed(digits)}</span>
      </div>
    </div>
  )
}

function ExpireDialog({ tz, initial, onSet, onCancel }: { tz: string; initial: number; onSet: (ms: number) => void; onCancel: () => void }) {
  // datetime-local works in the browser's zone; show the server zone in the title so it is clear which clock it means
  const toLocal = (ms: number) => { const d = new Date(ms); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16) }
  const [v, setV] = useState(toLocal(initial))
  return (
    <Dialog title="Expiration" onClose={onCancel} actions={[{ label: 'Cancel', onClick: onCancel }, { label: 'OK', primary: true, onClick: () => onSet(new Date(v).getTime()), testId: 'expire-ok' }]}>
      <div>Order expires at (your device time; server time zone is {tz}):</div>
      <input type="datetime-local" value={v} onChange={(e) => setV(e.target.value)} data-testid="expire-input" />
    </Dialog>
  )
}
