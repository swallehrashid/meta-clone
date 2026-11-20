import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { getEquity, type Deal, type EquityPoint } from '../lib/trade'
import { useT } from './ctx'
import { duration, money, stamp, tzParts } from './format'
import { IDownload, IPrint } from './icons'
import { IconBtn, Page } from './ui'

type RTab = 'summary' | 'pl' | 'ls' | 'symbols' | 'risks'
const TABS: [RTab, string][] = [['summary', 'Summary'], ['pl', 'Profit & Loss'], ['ls', 'Long & Short'], ['symbols', 'Symbols'], ['risks', 'Risks']]
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const PALETTE = ['#4a8ef7', '#ef6b3a', '#36b24a', '#f5b800', '#b05cf0', '#2fbfbf', '#e5534b', '#9aa0a6']

export interface Stats {
  n: number; gp: number; gl: number; comm: number; net: number; sharpe: number; pf: number; rf: number; maxDdPct: number; maxDdMoney: number
  maxLoad: number; perWeek: number; avgHold: number; best: number; worst: number; winsRow: number; lossRow: number; profitRow: number; lossRowMoney: number
  avgWin: number; avgLoss: number; longN: number; shortN: number; long: Deal[]; short: Deal[]
}

export function stats(deals: Deal[], eq: EquityPoint[], initial: number): Stats {
  const d = [...deals].sort((a, b) => a.close_ms - b.close_ms)
  const net = d.map((x) => x.net)
  const n = d.length, sum = net.reduce((a, b) => a + b, 0), mean = n ? sum / n : 0
  const sd = n > 1 ? Math.sqrt(net.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : 0
  const gp = d.filter((x) => x.gross > 0).reduce((a, x) => a + x.gross, 0), gl = d.filter((x) => x.gross < 0).reduce((a, x) => a + x.gross, 0)
  let peak = initial, bal = initial, ddM = 0, ddP = 0
  for (const v of net) { bal += v; peak = Math.max(peak, bal); ddM = Math.max(ddM, peak - bal); ddP = Math.max(ddP, peak ? (peak - bal) / peak * 100 : 0) }
  for (const p of eq) { peak = Math.max(peak, p.equity); ddM = Math.max(ddM, peak - p.equity); ddP = Math.max(ddP, peak ? (peak - p.equity) / peak * 100 : 0) }
  let w = 0, l = 0, wr = 0, lr = 0, ps = 0, ls = 0, pr = 0, lrm = 0
  for (const v of net) {
    if (v > 0) { w++; l = 0; ps += v; ls = 0 } else { l++; w = 0; ls += v; ps = 0 }
    wr = Math.max(wr, w); lr = Math.max(lr, l); pr = Math.max(pr, ps); lrm = Math.min(lrm, ls)
  }
  const span = n ? (d[n - 1].close_ms - d[0].open_ms) / (7 * 86400_000) : 0
  const wins = net.filter((v) => v > 0), losses = net.filter((v) => v <= 0)
  return {
    n, gp, gl, comm: -d.reduce((a, x) => a + x.commission, 0), net: sum, sharpe: sd ? mean / sd : 0, pf: gl ? gp / -gl : gp ? Infinity : 0,
    rf: ddM ? sum / ddM : 0, maxDdPct: ddP, maxDdMoney: ddM, maxLoad: eq.reduce((m, p) => Math.max(m, p.equity ? (p.margin / p.equity) * 100 : 0), 0),
    perWeek: n ? n / Math.max(1, span) : 0, avgHold: n ? d.reduce((a, x) => a + x.duration_s, 0) / n : 0,
    best: n ? Math.max(...net) : 0, worst: n ? Math.min(...net) : 0, winsRow: wr, lossRow: lr, profitRow: pr, lossRowMoney: lrm,
    avgWin: wins.length ? wins.reduce((a, b) => a + b, 0) / wins.length : 0, avgLoss: losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : 0,
    longN: d.filter((x) => x.side === 'buy').length, shortN: d.filter((x) => x.side === 'sell').length,
    long: d.filter((x) => x.side === 'buy'), short: d.filter((x) => x.side === 'sell'),
  }
}

export function ReportPage({ onBack }: { onBack: () => void }) {
  const t = useT()
  const [tab, setTab] = useState<RTab>('summary')
  const [eq, setEq] = useState<EquityPoint[]>([])
  const [money2, setMoney2] = useState(true)
  const [risk, setRisk] = useState<'dd' | 'load'>('dd')
  useEffect(() => { getEquity().then((r) => setEq(r.points)).catch(() => undefined) }, [])
  const a = t.trade?.account
  const initial = a?.initial_balance ?? 0
  const s = useMemo(() => stats(t.deals, eq, initial), [t.deals, eq, initial])
  const tz = t.cfg.tz
  const download = () => {
    const rows = [...t.deals].sort((x, y) => x.close_ms - y.close_ms).map((d) => `<tr><td>${stamp(d.open_ms, tz)}</td><td>${d.position_id}</td><td>${d.symbol}</td><td>${d.side}</td><td>${d.volume}</td><td>${d.open_price}</td><td>${stamp(d.close_ms, tz)}</td><td>${d.close_price}</td><td>${(-d.commission).toFixed(2)}</td><td>0.00</td><td>${d.gross.toFixed(2)}</td></tr>`).join('')
    const html = `<!doctype html><meta charset=utf-8><title>ReportHistory-${t.cfg.login}</title><style>body{font:13px Roboto,Arial,sans-serif}td,th{border:1px solid #ccc;padding:3px 6px}table{border-collapse:collapse}</style>
<h2>Trade History Report</h2><p>Name: ${t.cfg.account_name}<br>Account: ${t.cfg.login} (USD, ${t.cfg.server_name}, demo)<br>Date: ${stamp(Date.now(), tz)}</p>
<h3>Positions</h3><table><tr><th>Time</th><th>Position</th><th>Symbol</th><th>Type</th><th>Volume</th><th>Price</th><th>Close time</th><th>Close price</th><th>Commission</th><th>Swap</th><th>Profit</th></tr>${rows}</table>
<h3>Results</h3><table><tr><td>Total Net Profit</td><td>${s.net.toFixed(2)}</td><td>Gross Profit</td><td>${s.gp.toFixed(2)}</td><td>Gross Loss</td><td>${s.gl.toFixed(2)}</td></tr>
<tr><td>Profit Factor</td><td>${isFinite(s.pf) ? s.pf.toFixed(2) : '–'}</td><td>Recovery Factor</td><td>${s.rf.toFixed(2)}</td><td>Sharpe Ratio</td><td>${s.sharpe.toFixed(2)}</td></tr>
<tr><td>Total Trades</td><td>${s.n}</td><td>Max drawdown</td><td>${s.maxDdMoney.toFixed(2)} (${s.maxDdPct.toFixed(2)}%)</td><td>Long / Short</td><td>${s.longN} / ${s.shortN}</td></tr></table>`
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }))
    const el = document.createElement('a'); el.href = url; el.download = `ReportHistory-${t.cfg.login}.html`; el.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    t.toast('Report saved to your downloads')
  }

  return (
    <Page title="Report" onBack={onBack} testId="report" actions={<>
      <IconBtn onClick={() => window.print()} label="Print" testId="r-print"><IPrint /></IconBtn>
      <IconBtn onClick={download} label="Download" testId="r-download"><IDownload /></IconBtn>
    </>}>
      <div className="rtabs">{TABS.map(([k, l]) => <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)} data-testid={`rtab-${k}`}>{l}</button>)}</div>
      <div className="rep" data-testid={`report-${tab}`}>
        {tab === 'summary' && <>
          <TotalRing s={s} />
          <Gauge label="Sharp Ratio" v={s.sharpe} max={5} />
          <Gauge label="Profit Factor" v={isFinite(s.pf) ? s.pf : 4} max={4} />
          <Gauge label="Recovery Factor" v={s.rf} max={7} />
          <Gauge label="Max. Drawdown" v={s.maxDdPct} max={100} pct />
          <Gauge label="Max. Deposit Load" v={s.maxLoad} max={100} pct />
          <div style={{ display: 'flex', gap: 24 }}>
            <div style={{ flex: 1 }}><Gauge label="Trades per Week" v={s.perWeek} max={10} /></div>
            <div style={{ flex: 1 }}><Gauge label="Average Hold Time" v={s.avgHold} max={86400} fmt={(x) => duration(x)} maxLabel="1d" /></div>
          </div>
          <div className="kp" style={{ marginTop: 26, alignItems: 'flex-start' }}>
            <div><b className="plain">{money(a?.balance)}</b><small><i className="dot" style={{ background: '#4a8ef7' }} />Balance</small></div>
            <div><b className="plain">{money(a?.equity)}</b><small><i className="dot" style={{ background: '#a57cf2' }} />Equity</small></div>
          </div>
          <LineChart series={[{ color: '#4a8ef7', pts: balancePts(t.deals, initial) }, { color: '#a57cf2', pts: eq.map((p) => [p.t_ms, p.equity] as [number, number]) }]} tz={tz} />
          <MonthStrip deals={t.deals} tz={tz} />
        </>}
        {tab === 'pl' && <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div className="kp" style={{ maxWidth: 230 }}>
              <div><b>{money(s.gp)}</b><small><i className="dot" style={{ background: '#36b24a' }} />Profit</small></div>
              <div><b>0.00</b><small>Dividends</small></div><div><b>0.00</b><small>Swaps</small></div>
              <div><b className={s.gl < 0 ? 'neg' : ''}>{money(s.gl)}</b><small><i className="dot" style={{ background: '#ef6b3a' }} />Loss</small></div>
              <div><b className={s.comm < 0 ? 'neg' : ''}>{money(s.comm)}</b><small>Commissions</small></div>
            </div>
            <Toggle a="Money" b="Deals" on={money2} set={setMoney2} />
          </div>
          <DayBars deals={t.deals} money={money2} tz={tz} />
          <MonthStrip deals={t.deals} tz={tz} />
          <TotalRing s={s} />
          <h4>By weekday</h4><WeekBars deals={t.deals} tz={tz} />
          <h4>By source</h4>
          <Donut parts={[{ v: t.deals.filter((d) => d.source !== 'bot').length, c: '#4a8ef7', l: 'Manual Trading' }, { v: t.deals.filter((d) => d.source === 'bot').length, c: '#a57cf2', l: 'Trading Robots' }, { v: 0, c: '#ef6b3a', l: 'Trading Signals' }]} center={String(s.n)} sub="Trades" />
        </>}
        {tab === 'ls' && <>
          <div className="kp">
            <div><b className="plain">{s.longN} ({s.n ? Math.round(s.longN / s.n * 100) : 0}%)</b><small><i className="dot" style={{ background: '#4a8ef7' }} />Long</small></div>
            <div><b className="plain">{s.shortN} ({s.n ? Math.round(s.shortN / s.n * 100) : 0}%)</b><small><i className="dot" style={{ background: '#ef6b3a' }} />Short</small></div>
          </div>
          <LineChart series={[{ color: '#4a8ef7', pts: cum(s.long) }, { color: '#ef6b3a', pts: cum(s.short) }]} tz={tz} />
          <Donut parts={[{ v: s.longN, c: '#4a8ef7', l: 'Long' }, { v: s.shortN, c: '#ef6b3a', l: 'Short' }]} center={`${s.n ? Math.round(s.longN / s.n * 100) : 0}%`} sub="Long Trades" />
          <SplitRow label="Netto P/L" l={sum(s.long)} r={sum(s.short)} />
          <SplitRow label="Average P/L" l={avg(s.long)} r={avg(s.short)} />
          <SplitRow label="Commissions" l={-s.long.reduce((x, d) => x + d.commission, 0)} r={-s.short.reduce((x, d) => x + d.commission, 0)} />
          <SplitRow label="Average Profit" l={avg(s.long.filter((d) => d.net > 0))} r={avg(s.short.filter((d) => d.net > 0))} />
          <SplitRow label="Average Loss" l={avg(s.long.filter((d) => d.net <= 0))} r={avg(s.short.filter((d) => d.net <= 0))} />
          <SplitRow label="Win rate %" l={rate(s.long)} r={rate(s.short)} pct />
        </>}
        {tab === 'symbols' && <SymbolsTab deals={t.deals} money={money2} setMoney={setMoney2} />}
        {tab === 'risks' && <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
            <div className="kp"><div><b className="plain">{money(a?.balance)}</b><small><i className="dot" style={{ background: '#4a8ef7' }} />Balance</small></div>
              <div><b className="plain">{(risk === 'dd' ? s.maxDdPct : s.maxLoad).toFixed(0)}%</b><small><i className="dot" style={{ background: '#ef6b3a' }} />{risk === 'dd' ? 'Drawdown' : 'Deposit Load'}</small></div></div>
            <Toggle a="Drawdown" b="Deposit Load" on={risk === 'dd'} set={(v) => setRisk(v ? 'dd' : 'load')} />
          </div>
          <LineChart series={[{ color: '#4a8ef7', pts: balancePts(t.deals, initial) }, { color: '#ef6b3a', pts: risk === 'dd' ? ddPts(eq) : eq.map((p) => [p.t_ms, p.equity ? p.margin / p.equity * 100 : 0] as [number, number]), right: true }]} tz={tz} />
          <BarPair l={`Best trade: ${money(s.best)}`} r={`Worst trade: ${money(s.worst)}`} lv={s.best} rv={-s.worst} />
          <BarPair l={`Max. consecutive wins: ${s.winsRow}`} r={`Max. consecutive losses: ${s.lossRow}`} lv={s.winsRow} rv={s.lossRow} />
          <BarPair l={`Max. consecutive profit: ${money(s.profitRow)}`} r={`Max. consecutive loss: ${money(s.lossRowMoney)}`} lv={s.profitRow} rv={-s.lossRowMoney} />
          <div className="kp" style={{ marginTop: 18 }}>
            <div><b className="plain">{money(s.avgWin)}</b><small><i className="dot" style={{ background: '#36b24a' }} />Avg. Profit</small></div>
            <div><b className="plain">{money(s.avgLoss)}</b><small><i className="dot" style={{ background: '#ef6b3a' }} />Avg. Loss</small></div>
            <div><b className="plain">{s.n ? (s.long.concat(s.short).filter((d) => d.net > 0).length / s.n * 100).toFixed(0) : 0}%</b><small>Win rate</small></div>
          </div>
        </>}
        {s.n === 0 && <div style={{ marginTop: 24, color: 'var(--dim)' }}>No closed trades yet: the report fills in as you trade.</div>}
      </div>
    </Page>
  )
}

const sum = (d: Deal[]) => d.reduce((a, x) => a + x.net, 0)
const avg = (d: Deal[]) => (d.length ? sum(d) / d.length : 0)
const rate = (d: Deal[]) => (d.length ? (d.filter((x) => x.net > 0).length / d.length) * 100 : 0)
function cum(d: Deal[]): [number, number][] { let c = 0; return d.map((x) => [x.close_ms, (c += x.net)]) }
function balancePts(deals: Deal[], initial: number): [number, number][] {
  const d = [...deals].sort((a, b) => a.close_ms - b.close_ms)
  let b = initial
  const out: [number, number][] = d.length ? [[d[0].open_ms, initial]] : []
  for (const x of d) out.push([x.close_ms, (b += x.net)])
  return out
}
function ddPts(eq: EquityPoint[]): [number, number][] { let peak = 0; return eq.map((p) => { peak = Math.max(peak, p.equity); return [p.t_ms, peak ? (peak - p.equity) / peak * 100 : 0] }) }

function Toggle({ a, b, on, set }: { a: string; b: string; on: boolean; set: (v: boolean) => void }) {
  return <div className="tog"><button className={on ? 'on' : ''} onClick={() => set(true)}>{a}</button><button className={!on ? 'on' : ''} onClick={() => set(false)}>{b}</button></div>
}

function Gauge({ label, v, max, pct, fmt, maxLabel }: { label: string; v: number; max: number; pct?: boolean; fmt?: (x: number) => string; maxLabel?: string }) {
  const f = Math.max(0, Math.min(1, v / max))
  return (
    <div className="gauge"><div className="gl">{label}{v ? `: ${fmt ? fmt(v) : pct ? v.toFixed(1) + '%' : v.toFixed(2)}` : ''}</div>
      <div className="gb"><i style={{ width: `${f * 100}%` }} /></div>
      <div className="gr"><span>{pct ? '0%' : fmt ? fmt(0) : '0'}</span><span>{maxLabel ?? (pct ? '100%' : max)}</span></div></div>
  )
}

function Donut({ parts, center, sub, size = 160, legend = true }: { parts: { v: number; c: string; l: string }[]; center: ReactNode; sub?: string; size?: number; legend?: boolean }) {
  const total = parts.reduce((a, p) => a + Math.abs(p.v), 0)
  const r = 64, C = 2 * Math.PI * r
  let off = 0
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', margin: '14px 0' }}>
      <svg width={size} height={size} viewBox="0 0 160 160">
        <circle cx="80" cy="80" r={r} fill="none" stroke="#4a4f57" strokeWidth="9" />
        {total > 0 && parts.map((p) => { const len = (Math.abs(p.v) / total) * C; const el = <circle key={p.l} cx="80" cy="80" r={r} fill="none" stroke={p.c} strokeWidth="9" strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-off} transform="rotate(-90 80 80)" />; off += len; return el })}
        <text x="80" y="78" textAnchor="middle" style={{ fill: 'var(--text)', fontSize: 18 }}>{center}</text>
        {sub && <text x="80" y="98" textAnchor="middle" style={{ fill: 'var(--text2)', fontSize: 11 }}>{sub}</text>}
      </svg>
      {legend && <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', justifyContent: 'center' }}>{parts.map((p) => <small key={p.l} style={{ display: 'flex', gap: 5, alignItems: 'center' }}><i className="dot" style={{ background: p.c }} />{p.l} {Number.isInteger(p.v) ? p.v : money(p.v)}</small>)}</div>}
    </div>
  )
}

function TotalRing({ s }: { s: Stats }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', alignItems: 'center', margin: '6px 0 4px' }}>
      <div className="kp" style={{ flexDirection: 'column', gap: 16 }}>
        <div><small><i className="dot" style={{ background: '#36b24a' }} />Gross Profit</small><b>{money(s.gp)}</b></div>
        <div><b>0.00</b><small>Swaps</small></div><div><b>0.00</b><small>Dividends</small></div>
      </div>
      <Donut parts={[{ v: s.gp, c: '#36b24a', l: '' }, { v: -s.gl, c: '#ef6b3a', l: '' }].filter((p) => p.v)} center={<tspan style={{ fill: s.net < 0 ? 'var(--red)' : 'var(--green)' }}>{money(s.net)}</tspan>} sub="Total" size={150} legend={false} />
      <div className="kp" style={{ flexDirection: 'column', gap: 16, alignItems: 'flex-end', textAlign: 'right' }}>
        <div style={{ alignItems: 'flex-end' }}><small><i className="dot" style={{ background: '#ef6b3a' }} />Gross Loss</small><b className={s.gl < 0 ? 'neg' : ''}>{money(s.gl)}</b></div>
        <div style={{ alignItems: 'flex-end' }}><b className={s.comm < 0 ? 'neg' : ''}>{money(s.comm)}</b><small>Commissions</small></div>
      </div>
    </div>
  )
}

function LineChart({ series, tz }: { series: { color: string; pts: [number, number][]; right?: boolean }[]; tz: string }) {
  const W = 336, H = 180
  const all = series.flatMap((s) => s.pts)
  if (!all.length) return <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ margin: '12px 0' }}><line x1="0" y1={H - 20} x2={W - 40} y2={H - 20} stroke="#4a8ef7" /><text x={W - 36} y={H - 16}>0.00</text></svg>
  const t0 = Math.min(...all.map((p) => p[0])), t1 = Math.max(...all.map((p) => p[0]), t0 + 1)
  const left = series.filter((s) => !s.right).flatMap((s) => s.pts.map((p) => p[1]))
  let lo = Math.min(...left, 0), hi = Math.max(...left, 1)
  if (hi === lo) hi = lo + 1
  const pad = (hi - lo) * 0.1; lo -= pad; hi += pad
  const x = (t: number) => ((t - t0) / (t1 - t0)) * (W - 50)
  const y = (v: number, right?: boolean) => (right ? H - 20 - (v / 100) * (H - 30) : H - 20 - ((v - lo) / (hi - lo)) * (H - 30))
  const ticks = [lo, (lo + hi) / 2, hi]
  const p0 = tzParts(t0, tz), p1 = tzParts(t1, tz)
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ margin: '12px 0' }} data-testid="report-chart">
      {ticks.map((v) => <g key={v}><line x1="0" x2={W - 50} y1={y(v)} y2={y(v)} stroke="#3a3a3a" strokeDasharray="3 3" /><text x={W - 46} y={y(v) + 3}>{Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + 'k' : v.toFixed(2)}</text></g>)}
      {series.map((s, i) => s.pts.length > 0 && <polyline key={i} fill="none" stroke={s.color} strokeWidth="1.8" points={s.pts.map((p) => `${x(p[0]).toFixed(1)},${y(p[1], s.right).toFixed(1)}`).join(' ')} />)}
      <text x="0" y={H - 4}>{`${p0.year}.${String(p0.month).padStart(2, '0')}.${String(p0.day).padStart(2, '0')}`}</text>
      <text x={W - 110} y={H - 4}>{`${p1.year}.${String(p1.month).padStart(2, '0')}.${String(p1.day).padStart(2, '0')}`}</text>
    </svg>
  )
}

function MonthStrip({ deals, tz }: { deals: Deal[]; tz: string }) {
  const year = deals.length ? tzParts(Math.max(...deals.map((d) => d.close_ms)), tz).year : new Date().getFullYear()
  const by = new Array(12).fill(0)
  for (const d of deals) { const p = tzParts(d.close_ms, tz); if (p.year === year) by[p.month - 1] += d.net }
  const total = by.reduce((a, b) => a + b, 0)
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(12, 1fr) auto', fontSize: 11, color: 'var(--text2)', borderLeft: '1px solid #666' }}>
        {MONTHS.map((m, i) => <div key={m} style={{ textAlign: 'center', padding: '4px 0', color: by[i] > 0 ? 'var(--green)' : by[i] < 0 ? 'var(--red)' : undefined }} title={money(by[i])}>{m}</div>)}
        <div style={{ borderLeft: '1px solid #666', padding: '4px 6px' }}>{year}</div>
      </div>
      <div style={{ textAlign: 'right', marginTop: 8, fontSize: 13 }}>Total: <span style={{ color: total < 0 ? 'var(--red)' : 'var(--blue)' }}>{money(total)}</span></div>
    </div>
  )
}

function DayBars({ deals, money: m, tz }: { deals: Deal[]; money: boolean; tz: string }) {
  const days = new Map<string, { p: number; l: number }>()
  for (const d of deals) {
    const q = tzParts(d.close_ms, tz), k = `${q.month}/${q.day}`
    const e = days.get(k) ?? { p: 0, l: 0 }
    if (d.net > 0) e.p += m ? d.net : 1; else e.l += m ? -d.net : 1
    days.set(k, e)
  }
  const arr = [...days.entries()].slice(-20)
  const max = Math.max(1, ...arr.map(([, e]) => Math.max(e.p, e.l)))
  const W = 336, H = 160, bw = Math.min(14, (W - 40) / Math.max(1, arr.length) / 2.4)
  return (
    <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ margin: '14px 0' }}>
      <line x1="0" x2={W} y1={H / 2} y2={H / 2} stroke="#555" />
      {arr.map(([k, e], i) => { const x = 10 + i * ((W - 40) / Math.max(1, arr.length)); return <g key={k}>
        <rect x={x} y={H / 2 - (e.p / max) * (H / 2 - 14)} width={bw} height={(e.p / max) * (H / 2 - 14)} fill="#36b24a" />
        <rect x={x + bw + 1} y={H / 2} width={bw} height={(e.l / max) * (H / 2 - 14)} fill="#ef6b3a" />
        {i % Math.ceil(arr.length / 5) === 0 && <text x={x} y={H - 2}>{k}</text>}</g> })}
      {!arr.length && <text x={W - 30} y={H / 2 - 4}>{m ? '0.00' : '0'}</text>}
    </svg>
  )
}

function WeekBars({ deals, tz }: { deals: Deal[]; tz: string }) {
  const by = new Array(7).fill(0)
  for (const d of deals) by[(tzParts(d.close_ms, tz).weekday + 6) % 7] += d.net
  const max = Math.max(1e-9, ...by.map(Math.abs))
  return (
    <svg width="100%" viewBox="0 0 336 120">
      <line x1="0" x2="336" y1="60" y2="60" stroke="#555" />
      {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((l, i) => { const h = (Math.abs(by[i]) / max) * 44; return <g key={i}>
        <rect x={20 + i * 45} y={by[i] >= 0 ? 60 - h : 60} width="18" height={h} fill={by[i] >= 0 ? '#36b24a' : '#ef6b3a'} /><text x={25 + i * 45} y="116">{l}</text></g> })}
    </svg>
  )
}

function SplitRow({ label, l, r, pct }: { label: string; l: number; r: number; pct?: boolean }) {
  const tot = Math.abs(l) + Math.abs(r)
  const f = (v: number) => (pct ? `${v.toFixed(0)}%` : money(v))
  return (
    <div style={{ margin: '14px 0' }}><div style={{ fontSize: 12 }}>{label}: {f(l + (pct ? 0 : r))}</div>
      <div className="bbar">{tot > 0 && <><i style={{ left: 0, width: `${(Math.abs(l) / tot) * 100}%`, background: '#4a8ef7' }} /><i style={{ right: 0, width: `${(Math.abs(r) / tot) * 100}%`, background: '#ef6b3a' }} /></>}</div>
      <div className="row2"><span>{f(l)}</span><span>{f(r)}</span></div></div>
  )
}

function BarPair({ l, r, lv, rv }: { l: string; r: string; lv: number; rv: number }) {
  const tot = Math.abs(lv) + Math.abs(rv)
  return (
    <div style={{ margin: '20px 0' }}><div style={{ fontSize: 12 }}>{l}</div>
      <div className="bbar">{tot > 0 && <><i style={{ left: 0, width: `${(Math.abs(lv) / tot) * 100}%`, background: '#36b24a' }} /><i style={{ right: 0, width: `${(Math.abs(rv) / tot) * 100}%`, background: '#ef6b3a' }} /></>}</div>
      <div style={{ textAlign: 'right', fontSize: 12 }}>{r}</div></div>
  )
}

function SymbolsTab({ deals, money: m, setMoney }: { deals: Deal[]; money: boolean; setMoney: (v: boolean) => void }) {
  const by = new Map<string, { n: number; v: number }>()
  for (const d of deals) { const e = by.get(d.symbol) ?? { n: 0, v: 0 }; e.n++; e.v += d.net; by.set(d.symbol, e) }
  const parts = [...by.entries()].map(([k, e], i) => ({ l: k, v: m ? Math.round(e.v * 100) / 100 : e.n, c: PALETTE[i % PALETTE.length] }))
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end' }}><Toggle a="Money" b="Deals" on={m} set={setMoney} /></div>
      <Donut parts={parts} center={String(m ? money(parts.reduce((a, p) => a + p.v, 0)) : deals.length)} sub={m ? 'Total' : 'Deals'} />
      <Donut parts={[{ v: deals.filter((d) => d.source === 'bot').length, c: '#a57cf2', l: 'Trading Robots' }, { v: 0, c: '#ef6b3a', l: 'Trading Signals' }, { v: deals.filter((d) => d.source !== 'bot').length, c: '#4a8ef7', l: 'Manual Trading' }]}
             center={String(deals.length)} sub="Trades" />
    </>
  )
}
