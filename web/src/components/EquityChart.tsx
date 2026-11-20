import { useEffect, useRef } from 'react'
import { ColorType, LineSeries, createChart, type IChartApi, type UTCTimestamp } from 'lightweight-charts'
import { theme } from '../theme'
import type { EquityPoint } from '../lib/trade'
import { hhmm } from '../lib/time'

export function EquityChart({ points, tz }: { points: EquityPoint[]; tz: string }) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<{ eq: ReturnType<IChartApi['addSeries']>; bal: ReturnType<IChartApi['addSeries']> } | null>(null)

  useEffect(() => {
    const c = createChart(box.current!, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: theme.panel }, textColor: theme.text, fontSize: 11, attributionLogo: false },
      grid: { vertLines: { color: theme.grid }, horzLines: { color: theme.grid } },
      rightPriceScale: { borderColor: theme.scaleBorder },
      timeScale: { borderColor: theme.scaleBorder, timeVisible: true, secondsVisible: false,
                   tickMarkFormatter: (t: unknown) => hhmm(t as number, tzRef.current) },
    })
    chart.current = c
    series.current = {
      bal: c.addSeries(LineSeries, { color: theme.textDim, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: 'Balance' }),
      eq: c.addSeries(LineSeries, { color: theme.accent, lineWidth: 2, priceLineVisible: false, title: 'Equity' }),
    }
    return () => { c.remove(); chart.current = null; series.current = null }
  }, [])
  const tzRef = useRef(tz)
  tzRef.current = tz

  useEffect(() => {
    if (!series.current) return
    const seen = new Set<number>()
    const eq: { time: UTCTimestamp; value: number }[] = [], bal: { time: UTCTimestamp; value: number }[] = []
    for (const p of points) {
      const t = Math.floor(p.t_ms / 1000)
      if (seen.has(t)) continue                       // the chart needs strictly increasing times
      seen.add(t)
      eq.push({ time: t as UTCTimestamp, value: p.equity }); bal.push({ time: t as UTCTimestamp, value: p.balance })
    }
    series.current.eq.setData(eq); series.current.bal.setData(bal)
    chart.current?.timeScale().fitContent()
  }, [points])

  return <div className="equity-chart" data-testid="equity-chart" ref={box} />
}
