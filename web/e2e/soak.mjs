// Long-running "phone session": the real app in Chrome at 390x844 with touch, used like a person would
// for N minutes against a live stack. Samples layout / connection / lag / memory and fails on regressions.
//   LAB_URL=http://127.0.0.1:8800 SOAK_MIN=30 OUT=/some/dir node e2e/soak.mjs
import { chromium } from 'playwright-core'
import { openChartLab } from './lab.mjs'
import fs from 'node:fs'

const URL_ = process.env.LAB_URL ?? 'http://127.0.0.1:8800'
const MIN = Number(process.env.SOAK_MIN ?? 30)
const OUT = process.env.OUT ?? '/tmp'
const SAMPLE_MS = 15000, ACTION_MS = 60000
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const log = (...a) => { const line = `[${((Date.now() - t0) / 60000).toFixed(1)}m] ${a.join(' ')}`; console.log(line); fs.appendFileSync(`${OUT}/soak.log`, line + '\n') }
const t0 = Date.now()

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
const page = await ctx.newPage()
const cdp = await ctx.newCDPSession(page)
await cdp.send('Performance.enable')
const errors = [], sockets = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
page.on('websocket', (ws) => { const r = { url: ws.url(), closed: false }; sockets.push(r); ws.on('close', () => { r.closed = true }) })
const st = () => page.evaluate(() => window.__lab.state())
const heap = async () => {
  await cdp.send('HeapProfiler.collectGarbage')
  const { metrics } = await cdp.send('Performance.getMetrics')
  return metrics.find((m) => m.name === 'JSHeapUsedSize').value / 1048576
}

await openChartLab(page, URL_)
const until = async (fn, ms, what) => { const t = Date.now(); while (Date.now() - t < ms) { try { const v = await fn(); if (v) return v } catch { /* retry */ } await sleep(200) } throw new Error('timeout: ' + what) }
await until(async () => (await st()).app?.status === 'open' && (await st())['pane:chart-0']?.bars > 50, 30000, 'initial load')

const samples = [], problems = [], calls = []
let lastAction = 0, actionNo = 0, badSince = null, worstBad = 0
let expectDelay = 300
const delays = [['delay-60', 60], ['delay-900', 900], ['delay-300', 300]]
const actions = [
  async () => { const [id, s] = delays[actionNo % 3]; await page.tap(`[data-testid=${id}]`); expectDelay = s; return `delay ${s}s` },
  async () => { const tf = ['M5', 'M15', 'H1', 'M1'][actionNo % 4]; await page.tap(`[data-testid=tf-${tf}]`); return `timeframe ${tf}` },
  async () => { const n = [2, 4, 1][actionNo % 3]; await page.tap(`[data-testid=layout-${n}]`); return `layout ${n}` },
  async () => { await page.tap('[data-testid=toggle-labels]'); return 'labels toggle' },
  async () => { await page.tap(actionNo % 2 ? '[data-testid=buy]' : '[data-testid=sell]'); calls.push(Date.now()); return 'practice call' },
  async () => { await page.selectOption('[data-testid=tz]', ['UTC', 'Europe/London', 'America/New_York'][actionNo % 3]); return 'timezone' },
  async () => { await page.tap('[data-testid=toggle-calls]'); await sleep(1500); await page.tap('[data-testid=toggle-calls]'); return 'calls panel' },
  async () => { await page.tap('[data-testid=toggle-ask]'); return 'ask line toggle' },
]

log(`soak start: ${MIN} min, phone viewport 390x844`)
const end = t0 + MIN * 60000
while (Date.now() < end) {
  if (Date.now() - lastAction >= ACTION_MS) {
    lastAction = Date.now()
    try { log('action:', await actions[actionNo % actions.length]()) } catch (e) { problems.push(`action ${actionNo} failed: ${e.message}`) }
    actionNo++
    await sleep(2500)
  }
  const s = await st(); const a = s.app
  const lag = Date.now() - a.cutoffMs
  const smp = { t: (Date.now() - t0) / 1000, status: a.status, overflow: a.scrollW > a.innerW, lagErr: lag - a.delay * 1000, delay: a.delay,
    heapMB: await heap(), dom: await page.evaluate(() => document.getElementsByTagName('*').length),
    bars: s['pane:chart-0']?.bars, panes: await page.locator('.charts .pane').count(), calls: a.calls, tf: a.tf }
  samples.push(smp)
  if (smp.status !== 'open') { badSince ??= Date.now() } else { if (badSince) worstBad = Math.max(worstBad, Date.now() - badSince); badSince = null }
  if (smp.overflow) problems.push(`horizontal overflow at ${smp.t.toFixed(0)}s`)
  if (smp.status === 'open' && Math.abs(smp.lagErr) > 3000 && Date.now() - lastAction > 6000) problems.push(`chart lag off by ${(smp.lagErr / 1000).toFixed(1)}s at ${smp.t.toFixed(0)}s (delay ${smp.delay})`)
  log(`status=${smp.status} lagErr=${(smp.lagErr / 1000).toFixed(2)}s heap=${smp.heapMB.toFixed(1)}MB dom=${smp.dom} bars=${smp.bars} panes=${smp.panes} calls=${smp.calls} overflow=${smp.overflow}`)
  await sleep(SAMPLE_MS)
}

// every practice call should have been graded by now (the last may still be inside its window)
const callsApi = await page.evaluate(async () => (await fetch('/calls?limit=1000')).json())
const mine = callsApi.calls.filter((c) => c.created_ms >= t0)
// A call is graded once its 5-minute outcome window has passed. On a chart that is `delay` seconds behind, the window
// ends (300 - delay) s after the call is made, so only a call that is STILL pending 30 s after that is stuck.
const stuck = mine.filter((c) => c.status === 'pending' && Date.now() > c.created_ms + (300 - c.delay_s) * 1000 + 30000)
if (stuck.length) problems.push(`${stuck.length} call(s) still pending well after their outcome window closed`)
if (worstBad > 15000) problems.push(`connection was down for ${(worstBad / 1000).toFixed(0)} s`)
if (errors.length) problems.push(`console errors: ${errors.slice(0, 3).join(' | ')}`)

const first = samples.slice(0, 3), last = samples.slice(-3)
const avg = (xs, k) => xs.reduce((a, x) => a + x[k], 0) / xs.length
const heapGrowth = avg(last, 'heapMB') - avg(first, 'heapMB')
const domGrowth = avg(last, 'dom') - avg(first, 'dom')
if (heapGrowth > 30) problems.push(`JS heap grew ${heapGrowth.toFixed(1)} MB`)
if (Math.max(...samples.map((x) => x.dom)) > 3000) problems.push(`DOM grew to ${Math.max(...samples.map((x) => x.dom))} nodes`)
const dropped = sockets.filter((x) => !x.closed).length

const summary = {
  minutes: ((Date.now() - t0) / 60000).toFixed(1), samples: samples.length, actions: actionNo, callsMade: mine.length,
  callsGraded: mine.filter((c) => c.status === 'final').length, overflowSamples: samples.filter((x) => x.overflow).length,
  maxAbsLagErrSeconds: Math.max(...samples.filter((x) => x.status === 'open').map((x) => Math.abs(x.lagErr))) / 1000,
  heapMB: { first: avg(first, 'heapMB').toFixed(1), last: avg(last, 'heapMB').toFixed(1), growth: heapGrowth.toFixed(1) },
  domNodes: { first: avg(first, 'dom').toFixed(0), last: avg(last, 'dom').toFixed(0), max: Math.max(...samples.map((x) => x.dom)) },
  socketsOpenedTotal: sockets.length, socketsStillOpen: dropped, longestDisconnectMs: worstBad, consoleErrors: errors.length, problems,
}
fs.writeFileSync(`${OUT}/soak-summary.json`, JSON.stringify(summary, null, 2))
await page.screenshot({ path: `${OUT}/soak-end.png` })
log('SUMMARY ' + JSON.stringify(summary))
await browser.close()
console.log(problems.length ? `\nSOAK FAILED: ${problems.length} problem(s)` : '\nSOAK PASSED')
process.exit(problems.length ? 1 : 0)
