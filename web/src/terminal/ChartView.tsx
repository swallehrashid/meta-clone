import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import {
  BarSeries, CandlestickSeries, ColorType, CrosshairMode, HistogramSeries, LineSeries, LineStyle, createChart,
  type IChartApi, type IPriceLine, type ISeriesApi, type MouseEventParams, type SeriesType, type Time, type UTCTimestamp,
} from 'lightweight-charts'
import { registerDebug } from '../lib/debug'
import type { MarketStore } from '../lib/store'
import { TF_SECONDS, type Bar, type Timeframe } from '../lib/types'
import { countdown, crossTime, dayMon, dayMonHm, tzParts } from './format'
import { byType } from './indicators'
import type { ChartType, IndicatorConf, TfName } from './prefs'

export interface Level {
  key: string
  price: number
  color: string
  label?: string
  dashed?: boolean
  draggable?: boolean
  onDrag?: (price: number) => void       // while dragging
  onDrop?: (price: number) => void       // released
}

export interface ChartColors { bg: string; text: string; grid: string; scale: string; up: string; down: string; ask: string; bid: string }

interface Props {
  symbol: string
  description: string
  store: MarketStore
  tz: string
  delayS: number
  tf: TfName
  kind: ChartType
  colors: ChartColors
  grid: boolean
  showAsk: boolean
  volumes: boolean
  ohlc: boolean
  separators: boolean
  crosshair: boolean
  indicators: IndicatorConf[]
  levels: Level[]
  onPickSymbol: () => void
  onPickTf: () => void
  onMore: () => void
}

/** W1 / MN are calendar periods: built here from D1 candles (week from Monday, month from the 1st, in the server zone). */
export function aggregate(bars: Bar[], tf: TfName, tz: string): Bar[] {
  if (tf !== 'W1' && tf !== 'MN') return bars
  const out: Bar[] = []
  let key = '', cur: Bar | null = null
  for (const b of bars) {
    const p = tzParts(b.time * 1000, tz)
    let k: string, start: number
    if (tf === 'MN') { k = `${p.year}-${p.month}`; start = Date.UTC(p.year, p.month - 1, 1) / 1000 }
    else {
      const back = (p.weekday + 6) % 7                     // days since Monday
      start = Date.UTC(p.year, p.month - 1, p.day - back) / 1000
      k = String(start)
    }
    if (k !== key || !cur) { cur = { time: start, open: b.open, high: b.high, low: b.low, close: b.close, ticks: b.ticks ?? 0 }; out.push(cur); key = k }
    else { cur.high = Math.max(cur.high, b.high); cur.low = Math.min(cur.low, b.low); cur.close = b.close; cur.ticks = (cur.ticks ?? 0) + (b.ticks ?? 0) }
  }
  return out
}

function periodEnd(lastTime: number, tf: TfName, tz: string): number {
  if (tf === 'W1') return lastTime + 7 * 86400
  if (tf === 'MN') { const p = tzParts(lastTime * 1000, 'UTC'); return Date.UTC(p.year, p.month, 1) / 1000 }
  void tz
  return lastTime + TF_SECONDS[tf as Timeframe]
}

type AnySeries = ISeriesApi<SeriesType>
const ohlc = (b: Bar) => ({ time: b.time as UTCTimestamp, open: b.open, high: b.high, low: b.low, close: b.close })
const pt = (b: Bar) => ({ time: b.time as UTCTimestamp, value: b.close })

export function ChartView(p: Props) {
  const box = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const mainRef = useRef<AnySeries | null>(null)
  const volRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const indRef = useRef<{ series: AnySeries[]; lines: IPriceLine[] }[]>([])
  const priceLines = useRef(new Map<string, IPriceLine>())
  const askLine = useRef<IPriceLine | null>(null)
  const bidLine = useRef<IPriceLine | null>(null)
  const barsRef = useRef<Bar[]>([])
  const [sv, setSv] = useState(0)
  const [geo, setGeo] = useState<{ tag: { y: number; price: string; left: string; w: number } | null; lv: Record<string, number | null>; seps: number[]; w: number; h: number; more: number }>({ tag: null, lv: {}, seps: [], w: 0, h: 0, more: 0 })
  const [hover, setHover] = useState<Bar | null>(null)
  const drag = useRef<{ key: string; price: number } | null>(null)
  const [dragPrice, setDragPrice] = useState<{ key: string; price: number } | null>(null)
  const opt = useRef(p)
  opt.current = p
  useSyncExternalStore(p.store.subscribe, p.store.getVersion)

  // ---- the chart (once) -------------------------------------------------------------------------
  useEffect(() => {
    const chart = createChart(box.current!, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: p.colors.bg }, textColor: p.colors.scale, fontSize: 12, fontFamily: 'Roboto, Arial, sans-serif',
                attributionLogo: true, panes: { separatorColor: '#444', separatorHoverColor: '#666', enableResize: true } },
      grid: { vertLines: { color: p.colors.grid, style: LineStyle.Dashed }, horzLines: { color: p.colors.grid, style: LineStyle.Dashed } },
      crosshair: { mode: CrosshairMode.Hidden },
      rightPriceScale: { borderColor: '#5a5a5a', scaleMargins: { top: 0.06, bottom: 0.06 }, minimumWidth: 64, entireTextOnly: true },
      timeScale: { borderColor: '#5a5a5a', timeVisible: true, secondsVisible: false, rightOffset: 8, barSpacing: 7, minBarSpacing: 1.5, allowBoldLabels: false,
                   tickMarkFormatter: (t: Time, k: number) => (k >= 3 ? dayMonHm(t as number, opt.current.tz) : dayMon(t as number, opt.current.tz)) },
      localization: { timeFormatter: (t: Time) => crossTime(t as number, opt.current.tz) },
    })
    chartRef.current = chart
    return () => { chart.remove(); chartRef.current = null; mainRef.current = null; volRef.current = null; indRef.current = []; priceLines.current.clear(); askLine.current = bidLine.current = null }
  }, [])                                                        // eslint-disable-line react-hooks/exhaustive-deps

  // ---- colours / grid -----------------------------------------------------------------------------
  useEffect(() => {
    chartRef.current?.applyOptions({
      layout: { background: { type: ColorType.Solid, color: p.colors.bg }, textColor: p.colors.scale },
      grid: { vertLines: { visible: p.grid, color: p.colors.grid }, horzLines: { visible: p.grid, color: p.colors.grid } },
    })
    mainRef.current?.applyOptions(seriesStyle(p.kind, p.colors) as never)
  }, [p.colors, p.grid, p.kind])

  // ---- main series (re-created when the chart type changes) -------------------------------------------
  useEffect(() => {
    const chart = chartRef.current!
    // keep trade levels (order price, SL, TP) on screen when they are within reach, as MT5 does
    const autoscaleInfoProvider = (base: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
      const r = base()
      const lv = opt.current.levels
      if (!r || !lv.length) return r
      const span = r.priceRange.maxValue - r.priceRange.minValue
      const reach = Math.max(4 * span, 60 * 10 ** -(opt.current.store.data.get(opt.current.symbol)?.digits ?? 5))
      let lo = r.priceRange.minValue, hi = r.priceRange.maxValue
      for (const l of lv) if (l.price > lo - reach && l.price < hi + reach) { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price) }
      return { priceRange: { minValue: lo, maxValue: hi } }
    }
    const common = { lastValueVisible: false, priceLineVisible: false, autoscaleInfoProvider }
    const s: AnySeries = p.kind === 'line' ? chart.addSeries(LineSeries, { ...common, ...seriesStyle('line', p.colors) })
      : p.kind === 'bars' ? chart.addSeries(BarSeries, { ...common, ...seriesStyle('bars', p.colors) })
        : chart.addSeries(CandlestickSeries, { ...common, ...seriesStyle('candles', p.colors) })
    mainRef.current = s
    setSv((v) => v + 1)
    return () => {
      priceLines.current.clear(); askLine.current = bidLine.current = null
      if (chartRef.current) { try { chart.removeSeries(s) } catch { /* gone */ } }
      mainRef.current = null
    }
  }, [p.kind])                                                  // eslint-disable-line react-hooks/exhaustive-deps

  // ---- tick volumes on the main chart -----------------------------------------------------------------
  useEffect(() => {
    const chart = chartRef.current!
    if (!p.volumes) return
    const v = chart.addSeries(HistogramSeries, { priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false, priceFormat: { type: 'volume' } })
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 }, visible: false })
    volRef.current = v
    setSv((x) => x + 1)
    return () => { if (chartRef.current) { try { chart.removeSeries(v) } catch { /* gone */ } } volRef.current = null }
  }, [p.volumes])

  // ---- data ---------------------------------------------------------------------------------------------
  const sym = p.symbol
  useEffect(() => {
    const s = mainRef.current, chart = chartRef.current
    if (!s || !chart) return
    let lastLen = -1
    const render = (reset: boolean) => {
      const d = p.store.data.get(sym)
      const bars = d ? aggregate(d.bars, opt.current.tf, opt.current.tz) : []
      barsRef.current = bars
      if (!d) { s.setData([]); return }
      s.applyOptions({ priceFormat: { type: 'price', precision: d.digits, minMove: 10 ** -d.digits } })
      const line = opt.current.kind === 'line'
      if (reset || bars.length !== lastLen) {
        s.setData((line ? bars.map(pt) : bars.map(ohlc)) as never)
        volRef.current?.setData(bars.map((b, i) => ({ time: b.time as UTCTimestamp, value: b.ticks ?? 0, color: i && b.close < bars[i - 1].close ? '#ef4f4c88' : '#26a69a88' })))
        if (reset) chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 60), to: bars.length + 8 })
      } else if (bars.length) {
        const b = bars[bars.length - 1]
        s.update((line ? pt(b) : ohlc(b)) as never)
        volRef.current?.update({ time: b.time as UTCTimestamp, value: b.ticks ?? 0, color: bars.length > 1 && b.close < bars[bars.length - 2].close ? '#ef4f4c88' : '#26a69a88' })
      }
      lastLen = bars.length
      drawIndicators()
      refreshBidAsk()
    }
    render(true)
    return p.store.onSymbol(sym, (e) => render(e.type === 'reset'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sym, p.store, sv, p.tf, p.tz])

  // ---- bid / ask lines ------------------------------------------------------------------------------------
  const refreshBidAsk = () => {
    const s = mainRef.current
    if (!s) return
    const t = p.store.data.get(sym)?.lastTick
    const want = (ref: { current: IPriceLine | null }, on: boolean, price: number | undefined, color: string, axis: boolean) => {
      if (!on || price === undefined) { if (ref.current) { try { s.removePriceLine(ref.current) } catch { /* gone */ } ref.current = null } return }
      if (ref.current) ref.current.applyOptions({ price })
      else ref.current = s.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: axis, title: '' })
    }
    want(bidLine, true, t?.bid, opt.current.colors.bid, false)
    want(askLine, opt.current.showAsk, t?.ask, opt.current.colors.ask, true)
  }
  useEffect(refreshBidAsk, [p.showAsk, sv, p.colors])           // eslint-disable-line react-hooks/exhaustive-deps

  // ---- indicators -----------------------------------------------------------------------------------------
  const indKey = JSON.stringify(p.indicators)
  const drawIndicators = () => {
    const chart = chartRef.current
    if (!chart) return
    const bars = barsRef.current
    const confs = opt.current.indicators
    if (indRef.current.length !== confs.length) return          // being rebuilt
    confs.forEach((c, i) => {
      const def = byType(c.type)
      const slot = indRef.current[i]
      if (!def || !slot) return
      const outs = def.calc(bars, c.params, [c.color, c.color2 ?? def.colors[1] ?? c.color])
      outs.forEach((o, j) => {
        const s = slot.series[j]
        if (!s) return
        const data = bars.map((b, k) => {
          const v = o.values[k]
          if (v === null || v === undefined || !isFinite(v)) return { time: b.time as UTCTimestamp }
          return o.kind === 'hist' ? { time: b.time as UTCTimestamp, value: v, color: o.colors?.[k] ?? o.color } : { time: b.time as UTCTimestamp, value: v }
        })
        s.setData(data as never)
      })
    })
  }
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const subWindows = [...new Set(p.indicators.filter((c) => c.window > 0).map((c) => c.window))].sort((a, b) => a - b)
    const made = p.indicators.map((c) => {
      const def = byType(c.type)
      if (!def) return { series: [], lines: [] }
      const pane = c.window > 0 ? subWindows.indexOf(c.window) + 1 : 0
      const outs = def.calc([], c.params, [c.color, c.color2 ?? def.colors[1] ?? c.color])
      const series = outs.map((o) => {
        const base = { lastValueVisible: c.window > 0, priceLineVisible: false }
        if (o.kind === 'hist') return chart.addSeries(HistogramSeries, { ...base, color: o.color }, pane) as AnySeries
        if (o.kind === 'dots') return chart.addSeries(LineSeries, { ...base, color: o.color, lineVisible: false, pointMarkersVisible: true, pointMarkersRadius: 1.6 }, pane) as AnySeries
        return chart.addSeries(LineSeries, { ...base, color: o.color, lineWidth: 1 }, pane) as AnySeries
      })
      const lines = (def.levels ?? []).map((lv) => series[0].createPriceLine({ price: lv, color: '#777', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: '' }))
      return { series, lines }
    })
    indRef.current = made
    drawIndicators()
    return () => {
      for (const m of made) for (const s of m.series) { if (chartRef.current) { try { chart.removeSeries(s) } catch { /* gone */ } } }
      indRef.current = []
    }
  }, [indKey, sv])                                               // eslint-disable-line react-hooks/exhaustive-deps

  // ---- trade levels (price lines with axis labels; the overlay draws their names and handles) --------------------
  useEffect(() => {
    const s = mainRef.current
    if (!s) return
    const want = new Map(p.levels.map((l) => [l.key, l]))
    for (const [k, line] of priceLines.current) if (!want.has(k)) { try { s.removePriceLine(line) } catch { /* gone */ } priceLines.current.delete(k) }
    for (const l of p.levels) {
      const price = dragPrice?.key === l.key ? dragPrice.price : l.price
      const cur = priceLines.current.get(l.key)
      const o = { price, color: l.color, lineWidth: 1 as const, lineStyle: l.dashed ? LineStyle.Dashed : LineStyle.Solid, axisLabelVisible: true, title: '' }
      if (cur) cur.applyOptions(o)
      else priceLines.current.set(l.key, s.createPriceLine(o))
    }
  }, [p.levels, sv, dragPrice])

  // ---- crosshair mode: the finger moves the crosshair instead of the chart --------------------------------------
  useEffect(() => {
    const chart = chartRef.current, el = box.current
    if (!chart || !el) return
    const lbl = { visible: true, labelVisible: true, color: '#a0a0a0', style: LineStyle.Dashed, width: 1 as const, labelBackgroundColor: '#e9e9e9' }
    chart.applyOptions({
      crosshair: p.crosshair ? { mode: CrosshairMode.Normal, vertLine: lbl, horzLine: lbl } : { mode: CrosshairMode.Hidden },
      handleScroll: !p.crosshair, handleScale: !p.crosshair,
    })
    if (!p.crosshair) { chart.clearCrosshairPosition(); setHover(null); return }
    const move = (e: PointerEvent) => {
      if (drag.current) return
      const r = el.getBoundingClientRect(), s = mainRef.current
      if (!s) return
      const x = e.clientX - r.left, y = e.clientY - r.top
      const time = chart.timeScale().coordinateToTime(x), price = s.coordinateToPrice(y)
      if (time !== null && price !== null) chart.setCrosshairPosition(price, time, s)
    }
    const down = (e: PointerEvent) => { if ((e.target as HTMLElement).closest('.hd')) return; move(e) }
    const mv = (e: PointerEvent) => { if (e.buttons || e.pointerType === 'mouse') move(e) }
    el.addEventListener('pointerdown', down)
    el.addEventListener('pointermove', mv)
    // land the crosshair in the middle of the chart, as MT5 does when it is switched on
    const s = mainRef.current, bars = barsRef.current
    if (s && bars.length) { const b = bars[Math.max(0, bars.length - 25)]; chart.setCrosshairPosition((b.high + b.low) / 2, b.time as UTCTimestamp, s) }
    return () => { el.removeEventListener('pointerdown', down); el.removeEventListener('pointermove', mv) }
  }, [p.crosshair, sv])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return
    const f = (e: MouseEventParams) => {
      const s = mainRef.current
      const d = s && e.seriesData.get(s) as { open?: number; high?: number; low?: number; close?: number; value?: number } | undefined
      if (d && e.time !== undefined) setHover({ time: e.time as number, open: d.open ?? d.value ?? 0, high: d.high ?? d.value ?? 0, low: d.low ?? d.value ?? 0, close: d.close ?? d.value ?? 0 })
      else setHover(null)
    }
    chart.subscribeCrosshairMove(f)
    return () => chart.unsubscribeCrosshairMove(f)
  }, [sv])

  // ---- geometry: price tag + countdown, level labels, separators -------------------------------------------------
  useEffect(() => {
    const tick = () => {
      const chart = chartRef.current, s = mainRef.current, el = box.current, d = p.store.data.get(sym), bars = barsRef.current
      if (!chart || !s || !el) return
      const w = el.clientWidth, h = el.clientHeight, sw = chart.priceScale('right').width()
      let tag = null
      const last = bars[bars.length - 1]
      if (d && last) {
        const y = s.priceToCoordinate(last.close)
        if (y !== null && y > 0 && y < h - 40) {
          const left = periodEnd(last.time, opt.current.tf, opt.current.tz) - (Date.now() / 1000 - opt.current.delayS)
          tag = { y, price: last.close.toFixed(d.digits), left: countdown(Math.min(Math.max(left, 0), 99 * 3600)), w: sw }
        }
      }
      const lv: Record<string, number | null> = {}
      for (const l of opt.current.levels) {
        const price = drag.current?.key === l.key ? drag.current.price : l.price
        lv[l.key] = s.priceToCoordinate(price)
      }
      const seps: number[] = []
      if (opt.current.separators && bars.length) {
        const range = chart.timeScale().getVisibleLogicalRange()
        const day = opt.current.tf === 'D1' || opt.current.tf === 'W1' || opt.current.tf === 'MN' ? 'month' : 'day'
        let prev = ''
        const from = Math.max(0, Math.floor(range?.from ?? 0)), to = Math.min(bars.length - 1, Math.ceil(range?.to ?? bars.length))
        for (let i = from; i <= to; i++) {
          const q = tzParts(bars[i].time * 1000, opt.current.tz)
          const k = day === 'day' ? `${q.year}-${q.month}-${q.day}` : `${q.year}-${q.month}`
          if (prev && k !== prev) { const x = chart.timeScale().timeToCoordinate(bars[i].time as UTCTimestamp); if (x !== null) seps.push(x) }
          prev = k
        }
      }
      const tsh = chart.timeScale().height()
      setGeo({ tag, lv, seps, w: w - sw, h, more: h - tsh - 24 })
    }
    tick()
    const id = setInterval(tick, 200)
    return () => clearInterval(id)
  })

  // ---- dragging a level's handle -------------------------------------------------------------------------------------
  const startDrag = (l: Level, e: React.PointerEvent) => {
    e.stopPropagation(); e.preventDefault()
    const el = box.current, s = mainRef.current, chart = chartRef.current
    if (!el || !s || !chart) return
    const digits = p.store.data.get(sym)?.digits ?? 5
    chart.applyOptions({ handleScroll: false, handleScale: false })
    drag.current = { key: l.key, price: l.price }
    const target = e.currentTarget as HTMLElement
    try { target.setPointerCapture(e.pointerId) } catch { /* not capturable */ }
    const mv = (ev: PointerEvent) => {
      const r = el.getBoundingClientRect()
      const pr = s.coordinateToPrice(ev.clientY - r.top)
      if (pr === null) return
      const price = Number(pr.toFixed(digits))
      drag.current = { key: l.key, price }
      setDragPrice({ key: l.key, price })
      l.onDrag?.(price)
    }
    const up = () => {
      target.removeEventListener('pointermove', mv); target.removeEventListener('pointerup', up); target.removeEventListener('pointercancel', up)
      chart.applyOptions({ handleScroll: !opt.current.crosshair, handleScale: !opt.current.crosshair })
      const d = drag.current
      drag.current = null
      setDragPrice(null)
      if (d && d.price !== l.price) l.onDrop?.(d.price)
    }
    target.addEventListener('pointermove', mv); target.addEventListener('pointerup', up); target.addEventListener('pointercancel', up)
  }

  useEffect(() => registerDebug('chart', () => ({
    symbol: p.symbol, tf: p.tf, kind: p.kind, bars: barsRef.current.length, askLine: !!askLine.current, bidLine: !!bidLine.current,
    levels: p.levels.map((l) => ({ key: l.key, price: l.price })), indicators: indRef.current.map((x) => x.series.length),
    panes: chartRef.current?.panes().length ?? 0, crosshair: p.crosshair, tag: geo.tag, volumes: !!volRef.current, seps: geo.seps.length,
  })))

  const d = p.store.data.get(sym)
  const show = hover ?? (p.ohlc ? barsRef.current[barsRef.current.length - 1] : null)
  const fx = (x: number) => (d ? x.toFixed(d.digits) : String(x))
  const handleX = useMemo(() => Math.round(geo.w * 0.62), [geo.w])
  return (
    <div className={`chartbox${p.crosshair ? ' xh' : ''}`} data-testid="chartbox">
      <div className="c" ref={box} />
      {geo.seps.map((x, i) => <div key={i} style={{ position: 'absolute', left: x, top: 0, height: geo.more, borderLeft: `1px dashed ${p.colors.grid}`, zIndex: 2, opacity: .9, pointerEvents: 'none' }} />)}
      <div className="ctitle">
        <span className="s" onClick={p.onPickSymbol} data-testid="chart-symbol">{p.symbol}<i>▼</i></span>
        <span onClick={p.onPickTf} style={{ pointerEvents: 'auto', cursor: 'pointer', color: 'var(--text2)' }}>{p.tf}</span>
        <div className="d">{p.description}</div>
        {p.ohlc && show && <div className="ohlc" data-testid="ohlc">O {fx(show.open)}  H {fx(show.high)}  L {fx(show.low)}  C {fx(show.close)}</div>}
      </div>
      {p.levels.map((l) => {
        const y = geo.lv[l.key]
        if (y === null || y === undefined || y < 0 || y > geo.h) return null
        return (
          <div key={l.key} className="lvl" style={{ top: y, width: geo.w, color: l.color }} data-testid={`lvl-${l.key}`}>
            {l.label && <span>{l.label}</span>}
            {l.draggable && <i className="hd" style={{ left: handleX - 10 }} onPointerDown={(e) => startDrag(l, e)} data-testid={`handle-${l.key}`} />}
          </div>
        )
      })}
      {geo.tag && <div className="lasttag" style={{ top: geo.tag.y - 17, width: geo.tag.w || 64 }}>{geo.tag.price}<br />{geo.tag.left}</div>}
      <button className="chartmore" style={{ top: geo.more }} onClick={p.onMore} aria-label="More" data-testid="chart-more">
        <svg width="20" height="6" viewBox="0 0 20 6" aria-hidden><circle cx="3" cy="3" r="2.2" fill="currentColor" /><circle cx="10" cy="3" r="2.2" fill="currentColor" /><circle cx="17" cy="3" r="2.2" fill="currentColor" /></svg>
      </button>
      {!d?.bars.length && <div className="nodata">{p.store.status === 'open' ? 'Waiting for data…' : 'Connecting…'}</div>}
    </div>
  )
}

function seriesStyle(kind: ChartType, c: ChartColors) {
  if (kind === 'line') return { color: c.up === '#ffffff' ? '#ffffff' : c.up, lineWidth: 1 as const }
  if (kind === 'bars') return { upColor: c.up, downColor: c.down, thinBars: false }
  return { upColor: c.up, downColor: c.down, borderUpColor: c.up, borderDownColor: c.down, wickUpColor: c.up, wickDownColor: c.down }
}
