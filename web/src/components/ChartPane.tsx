import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import {
  CandlestickSeries, ColorType, CrosshairMode, LineStyle, createChart, createSeriesMarkers,
  type IChartApi, type IPriceLine, type ISeriesApi, type ISeriesMarkersPluginApi, type SeriesMarker,
  type Time, type UTCTimestamp,
} from 'lightweight-charts'
import { theme } from '../theme'
import { registerDebug } from '../lib/debug'
import { callMarkers, labelMarkers, mergeMarkers } from '../lib/markers'
import { dragPrice, hitLevel, levelsOf, type Level } from '../lib/levels'
import type { TOrder, TPosition } from '../lib/trade'
import type { MarketStore } from '../lib/store'
import { fullTime, hhmmss, tickMark } from '../lib/time'
import type { Bar, Call, Direction, Horizon, Label, Timeframe } from '../lib/types'

export interface PaneProps {
  symbol: string
  store: MarketStore
  tz: string
  showBid: boolean
  showAsk: boolean
  showLabels: boolean
  labelDir: Direction
  labelHorizon: Horizon
  labels: Label[]
  calls: Call[]
  active: boolean
  live?: boolean                    // the peek pane: real-time data, drawn with an orange frame
  onActivate?: () => void
  testId: string
  positions?: TPosition[]
  orders?: TOrder[]
  /** called after the user drags an SL / TP / pending-order line and lets go */
  onMoveLevel?: (level: Level, price: number) => void
}

const NO_POS: TPosition[] = []
const NO_ORD: TOrder[] = []

const ohlc = (b: Bar) => ({ time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })

export function ChartPane(p: PaneProps) {
  const box = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const lines = useRef<{ bid: IPriceLine | null; ask: IPriceLine | null }>({ bid: null, ask: null })
  const opts = useRef({ showBid: p.showBid, showAsk: p.showAsk, tz: p.tz })
  opts.current = { showBid: p.showBid, showAsk: p.showAsk, tz: p.tz }
  const markerCount = useRef({ labels: 0, calls: 0 })
  const tradeLines = useRef(new Map<string, IPriceLine>())
  const levelsRef = useRef<Level[]>([])
  const dragging = useRef<{ level: Level; pointerId: number; price: number } | null>(null)
  const onMoveRef = useRef(p.onMoveLevel)
  onMoveRef.current = p.onMoveLevel

  // ---- create the chart once --------------------------------------------------------
  useEffect(() => {
    const chart = createChart(box.current!, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: theme.background }, textColor: theme.text,
                fontSize: 12, attributionLogo: true },     // keep the TradingView attribution visible
      grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
      crosshair: { mode: CrosshairMode.Normal, vertLine: { color: theme.crosshair }, horzLine: { color: theme.crosshair } },
      rightPriceScale: { borderColor: theme.scaleBorder },
      timeScale: { borderColor: theme.scaleBorder, timeVisible: true, secondsVisible: false, rightOffset: 6,
                   tickMarkFormatter: (t: Time, kind: number) => tickMark(t as number, kind, opts.current.tz) },
      localization: { timeFormatter: (t: Time) => fullTime(t as number, opts.current.tz) },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: theme.candleUp, downColor: theme.candleDown,
      borderUpColor: theme.candleUp, borderDownColor: theme.candleDown,
      wickUpColor: theme.candleUp, wickDownColor: theme.candleDown,
      lastValueVisible: false, priceLineVisible: false,       // we draw MT5-style bid / ask lines ourselves
      // Keep SL / TP / pending-order lines on screen (a line you cannot see cannot be dragged) - but only the
      // ones within reach, so a stop 500 pips away does not flatten the candles.
      autoscaleInfoProvider: (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = base()
        const lv = levelsRef.current
        if (!r || !lv.length) return r
        const pip = p.store.data.get(p.symbol)?.pip ?? 1e-4
        const reach = Math.max(3 * (r.priceRange.maxValue - r.priceRange.minValue), 40 * pip)
        let lo = r.priceRange.minValue, hi = r.priceRange.maxValue
        for (const l of lv) {
          if (l.price > lo - reach && l.price < hi + reach) { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price) }
        }
        return { priceRange: { minValue: lo - (hi - lo) * 0.04, maxValue: hi + (hi - lo) * 0.04 } }
      },
    })
    chartRef.current = chart
    seriesRef.current = series
    markersRef.current = createSeriesMarkers(series, [])
    return () => {
      chart.remove()
      chartRef.current = seriesRef.current = markersRef.current = null
      lines.current = { bid: null, ask: null }
      tradeLines.current.clear()
    }
  }, [])

  // ---- bid / ask lines --------------------------------------------------------------
  const refreshLines = () => {
    const series = seriesRef.current
    if (!series) return
    const t = p.store.data.get(p.symbol)?.lastTick
    const want = (name: 'bid' | 'ask', on: boolean, price: number | undefined, color: string, title: string) => {
      const cur = lines.current[name]
      if (!on || price === undefined) {
        if (cur) { series.removePriceLine(cur); lines.current[name] = null }
        return
      }
      if (cur) cur.applyOptions({ price })
      else lines.current[name] = series.createPriceLine({
        price, color, lineWidth: 1, lineStyle: name === 'bid' ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true, title,
      })
    }
    want('bid', opts.current.showBid, t?.bid, theme.bidLine, 'Bid')
    want('ask', opts.current.showAsk, t?.ask, theme.askLine, 'Ask')
  }

  // ---- feed data from the store -----------------------------------------------------
  useEffect(() => {
    const series = seriesRef.current!, chart = chartRef.current!
    const render = () => {
      const d = p.store.data.get(p.symbol)
      if (!d) { series.setData([]); refreshLines(); return }
      series.applyOptions({ priceFormat: { type: 'price', precision: d.digits, minMove: 10 ** -d.digits } })
      series.setData(d.bars.map(ohlc))
      const n = d.bars.length
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 100), to: n + 6 })
      refreshLines()
    }
    render()                                           // data may already be there (symbol switch)
    return p.store.onSymbol(p.symbol, (e) => {
      if (e.type === 'reset') render()
      else { for (const b of e.bars) series.update(ohlc(b)); refreshLines() }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.symbol, p.store])

  useEffect(refreshLines, [p.showBid, p.showAsk])      // eslint-disable-line react-hooks/exhaustive-deps

  // ---- open positions and pending orders drawn as price lines (entry / SL / TP / order) -------------
  const positions = p.positions ?? NO_POS
  const orders = p.orders ?? NO_ORD
  const myPositions = useMemo(() => positions.filter((x) => x.symbol === p.symbol), [positions, p.symbol])
  const myOrders = useMemo(() => orders.filter((x) => x.symbol === p.symbol && x.kind === 'open'), [orders, p.symbol])
  useEffect(() => {
    const series = seriesRef.current
    if (!series) return
    const want = new Map<string, { price: number; color: string; style: LineStyle; title: string }>()
    for (const x of myPositions) {
      const tag = `#${x.id} ${x.side.toUpperCase()} ${x.volume}`
      want.set(`p${x.id}:open`, { price: x.open_price, color: x.side === 'buy' ? theme.callBuy : theme.callSell, style: LineStyle.Dotted, title: tag })
      if (x.sl !== null) want.set(`p${x.id}:sl`, { price: x.sl, color: theme.danger, style: LineStyle.Dashed, title: `SL #${x.id}` })
      if (x.tp !== null) want.set(`p${x.id}:tp`, { price: x.tp, color: theme.labelWin, style: LineStyle.Dashed, title: `TP #${x.id}` })
    }
    for (const o of myOrders) {
      if (o.price !== null) want.set(`o${o.id}:price`, { price: o.price, color: theme.accent, style: LineStyle.Dotted, title: `#${o.id} ${o.side.toUpperCase()} ${o.type.toUpperCase()} ${o.volume}` })
      if (o.sl !== null) want.set(`o${o.id}:sl`, { price: o.sl, color: theme.danger, style: LineStyle.Dashed, title: `SL #${o.id}` })
      if (o.tp !== null) want.set(`o${o.id}:tp`, { price: o.tp, color: theme.labelWin, style: LineStyle.Dashed, title: `TP #${o.id}` })
    }
    for (const [k, line] of tradeLines.current) {
      if (!want.has(k)) { series.removePriceLine(line); tradeLines.current.delete(k) }
    }
    for (const [k, w] of want) {
      if (dragging.current?.level.key === k) continue                      // the user is holding this one
      const cur = tradeLines.current.get(k)
      if (cur) cur.applyOptions({ price: w.price, title: w.title })
      else tradeLines.current.set(k, series.createPriceLine({ price: w.price, color: w.color, lineWidth: 1, lineStyle: w.style, axisLabelVisible: true, title: w.title }))
    }
    levelsRef.current = levelsOf(myPositions, myOrders.map((o) => ({ id: o.id, price: o.price, sl: o.sl, tp: o.tp })))
    chartRef.current?.priceScale('right').applyOptions({ autoScale: true })        // re-fit so every level is visible
  }, [myPositions, myOrders])

  // ---- drag SL / TP / pending price with mouse or finger ------------------------------------------
  useEffect(() => {
    const el = box.current
    if (!el) return
    const toY = (price: number) => seriesRef.current?.priceToCoordinate(price) ?? null
    const row = (e: PointerEvent) => e.clientY - el.getBoundingClientRect().top
    const down = (e: PointerEvent) => {
      if (!onMoveRef.current || !levelsRef.current.length) return
      const lvl = hitLevel(levelsRef.current, row(e), toY, e.pointerType === 'touch' ? 26 : 10)
      if (!lvl) return
      e.stopPropagation(); e.preventDefault()
      dragging.current = { level: lvl, pointerId: e.pointerId, price: lvl.price }
      chartRef.current?.applyOptions({ handleScroll: false, handleScale: false })     // do not pan the chart while dragging
      try { el.setPointerCapture(e.pointerId) } catch { /* not capturable */ }
    }
    const move = (e: PointerEvent) => {
      const d = dragging.current
      if (!d) {
        if (e.pointerType === 'mouse') el.style.cursor = hitLevel(levelsRef.current, row(e), toY, 10) ? 'ns-resize' : ''
        return
      }
      const digits = p.store.data.get(p.symbol)?.digits ?? 5
      const price = dragPrice(row(e), (y) => seriesRef.current?.coordinateToPrice(y) ?? null, digits)
      if (price === null) return
      d.price = price
      tradeLines.current.get(d.level.key)?.applyOptions({ price })
    }
    const finish = (e: PointerEvent, cancelled: boolean) => {
      const d = dragging.current
      if (!d) return
      dragging.current = null
      chartRef.current?.applyOptions({ handleScroll: true, handleScale: true })
      try { el.releasePointerCapture(d.pointerId) } catch { /* already released */ }
      if (!cancelled && Math.abs(d.price - d.level.price) > 0) onMoveRef.current?.(d.level, d.price)
      else tradeLines.current.get(d.level.key)?.applyOptions({ price: d.level.price })
      void e
    }
    const up = (e: PointerEvent) => finish(e, false)
    const cancel = (e: PointerEvent) => finish(e, true)
    el.addEventListener('pointerdown', down, true)       // capture phase: runs before the chart sees the gesture
    el.addEventListener('pointermove', move)
    el.addEventListener('pointerup', up)
    el.addEventListener('pointercancel', cancel)
    return () => {
      el.removeEventListener('pointerdown', down, true); el.removeEventListener('pointermove', move)
      el.removeEventListener('pointerup', up); el.removeEventListener('pointercancel', cancel)
    }
  }, [p.store, p.symbol])

  // ---- timezone ---------------------------------------------------------------------
  useEffect(() => {
    chartRef.current?.applyOptions({
      timeScale: { tickMarkFormatter: (t: Time, kind: number) => tickMark(t as number, kind, p.tz) },
      localization: { timeFormatter: (t: Time) => fullTime(t as number, p.tz) },
    })
  }, [p.tz])

  // ---- markers: practice calls always, labels only behind the toggle -----------------
  useSyncExternalStore(p.store.subscribe, p.store.getVersion)   // re-render the header on store changes
  const tf: Timeframe = p.store.data.get(p.symbol)?.tf ?? 'M1'
  const lm = useMemo(() => (p.showLabels ? labelMarkers(p.labels, tf, p.labelDir, p.labelHorizon) : []),
                      [p.showLabels, p.labels, tf, p.labelDir, p.labelHorizon])
  const cm = useMemo(() => callMarkers(p.calls, p.symbol, tf), [p.calls, p.symbol, tf])
  const markers = useMemo(() => mergeMarkers(lm, cm), [lm, cm])
  useEffect(() => {
    markerCount.current = { labels: lm.length, calls: cm.length }
    markersRef.current?.setMarkers(markers as SeriesMarker<Time>[])
  }, [markers, lm, cm])

  useEffect(() => registerDebug(`pane:${p.testId}`, () => ({
    symbol: p.symbol, bars: p.store.data.get(p.symbol)?.bars.length ?? 0,
    labelMarkers: markerCount.current.labels, callMarkers: markerCount.current.calls,
    lastClose: p.store.data.get(p.symbol)?.bars.at(-1)?.close ?? null,
    lastBarTime: p.store.data.get(p.symbol)?.bars.at(-1)?.time ?? null,
    bidLine: !!lines.current.bid, askLine: !!lines.current.ask,
    tradeLines: tradeLines.current.size,
    levels: levelsRef.current.map((l) => {
      const r = box.current?.getBoundingClientRect()
      const y = seriesRef.current?.priceToCoordinate(l.price) ?? null
      return { key: l.key, price: l.price, pageX: r ? r.left + r.width / 2 : null, pageY: r && y !== null ? r.top + y : null }
    }),
  })), [p.symbol, p.store, p.testId])

  // ---- header overlay ---------------------------------------------------------------
  const d = p.store.data.get(p.symbol)
  const t = d?.lastTick
  const spread = t && d ? ((t.ask - t.bid) / d.pip).toFixed(1) : '–'
  const f = (x: number | undefined) => (x !== undefined && d ? x.toFixed(d.digits) : '–')
  return (
    <div className={`pane${p.active ? ' active' : ''}${p.live ? ' live' : ''}`} data-testid={p.testId}
         onPointerDown={p.onActivate}>
      <div className="chart" ref={box} />
      <div className="pane-head">
        <b>{p.symbol}</b> <span className="dim">{d?.tf ?? tf}</span>
        {p.live && <span className="tag live-tag">LIVE · PEEK</span>}
        <div className="quote">
          <span style={{ color: theme.bidLine }}>{f(t?.bid)}</span>
          <span className="dim"> / </span>
          <span style={{ color: theme.askLine }}>{f(t?.ask)}</span>
          <span className="dim sp"> · {spread}p</span>
        </div>
        {p.live && <div className="dim small">{d ? hhmmss(Math.floor(p.store.cutoffMs / 1000), p.tz) : ''}</div>}
      </div>
    </div>
  )
}
