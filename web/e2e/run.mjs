// End-to-end check of the built app in real Chrome at phone size, against a running stack.
//   LAB_URL=http://127.0.0.1:8800 SHOTS=/some/dir node e2e/run.mjs
// Exits non-zero if any check fails.
import { chromium } from 'playwright-core'
import { openChartLab } from './lab.mjs'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const URL_ = process.env.LAB_URL ?? 'http://127.0.0.1:8800'
const SHOTS = process.env.SHOTS ?? '/tmp'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

let failed = 0
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
  if (!ok) failed++
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
const page = await ctx.newPage()
const errors = [], sockets = [], requests = []
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
page.on('websocket', (ws) => { const r = { url: ws.url(), closed: false }; sockets.push(r); ws.on('close', () => { r.closed = true }) })
page.on('request', (r) => requests.push({ url: r.url(), method: r.method(), body: r.postData() }))

const st = () => page.evaluate(() => window.__lab.state())
const until = async (fn, ms = 20000, what = 'condition') => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) { try { const v = await fn(); if (v) return v } catch { /* retry */ } await sleep(150) }
  throw new Error('timeout waiting for ' + what)
}
const api = (p, init) => page.evaluate(async ([p, init]) => (await fetch(p, init)).json(), [p, init])
const box = async (sel) => (await page.locator(sel).first().boundingBox())

try {
  // ------------------------------------------------------------------ 1. loads, attribution, no overflow
  await openChartLab(page, URL_)
  await page.evaluate(() => { window.__noReload = 1 })
  await until(async () => { const s = await st(); return s.app?.status === 'open' && s['pane:chart-0']?.bars > 100 }, 30000, 'first chart')
  let s = await st()
  check('chart loads with history', s['pane:chart-0'].bars > 100, `${s['pane:chart-0'].bars} candles`)
  check('no horizontal overflow (390px phone)', s.app.scrollW <= s.app.innerW, `${s.app.scrollW} <= ${s.app.innerW}`)
  check('TradingView attribution visible', await page.locator('a#tv-attr-logo').first().isVisible().catch(() => false))
  await page.screenshot({ path: `${SHOTS}/1_default.png` })

  // ------------------------------------------------------------------ 2. delay is real
  s = await st()
  const lag = Date.now() - s.app.cutoffMs
  check('delayed chart is 5 min behind real time (±1.5 s)', Math.abs(lag - 300000) < 1500, `lag ${(lag / 1000).toFixed(2)} s`)
  check('newest candle is not newer than the cutoff', s['pane:chart-0'].lastBarTime * 1000 <= s.app.cutoffMs)
  const c0 = s.app.cutoffMs; await sleep(2500)
  check('chart time keeps advancing', (await st()).app.cutoffMs > c0 + 1500)

  // ------------------------------------------------------------------ 3. phone ergonomics
  const buy = await box('[data-testid=buy]'), sell = await box('[data-testid=sell]')
  check('BUY / SELL tap targets are large (>= 56 px tall, >= 88 px wide)', buy.height >= 56 && sell.height >= 56 && buy.width >= 88 && sell.width >= 88, `${buy.width}x${buy.height}`)
  const small = await page.$$eval('.toolbar button, .toolbar select, .toolbar input', (els) => els.map((e) => e.getBoundingClientRect()).filter((r) => r.height < 40 || r.width < 32).length)
  check('all toolbar controls >= 40 px tall', small === 0, `${small} too small`)

  // ------------------------------------------------------------------ 4. delay switching, no reload, one socket
  const delayedSockets = () => sockets.filter((x) => x.url.includes('/ws/stream'))
  for (const [id, sec] of [['delay-60', 60], ['delay-900', 900], ['delay-300', 300]]) {
    await page.tap(`[data-testid=${id}]`)
    await until(async () => { const a = (await st()).app; return a.delay === sec && Math.abs(Date.now() - a.cutoffMs - sec * 1000) < 2500 }, 8000, `delay ${sec}`)
    const a = (await st()).app
    check(`switch to ${sec}s: chart now ${((Date.now() - a.cutoffMs) / 1000).toFixed(1)} s behind`, Math.abs(Date.now() - a.cutoffMs - sec * 1000) < 2500)
  }
  await page.fill('[data-testid=delay-custom]', '7m'); await page.tap('[data-testid=delay-set]')
  await until(async () => (await st()).app.delay === 420, 8000, 'custom 7m')
  check('custom delay "7m" accepted', true)
  await page.fill('[data-testid=delay-custom]', 'abc'); await page.tap('[data-testid=delay-set]')
  check('invalid custom delay rejected and unchanged', (await st()).app.delay === 420 && (await page.locator('.toolbar .err').count()) > 0)
  await page.tap('[data-testid=delay-300]'); await until(async () => (await st()).app.delay === 300, 8000)
  check('no page reload while switching delays', await page.evaluate(() => window.__noReload === 1))
  check('delay changes reused ONE websocket', delayedSockets().length === 1, `${delayedSockets().length} sockets opened`)

  // ------------------------------------------------------------------ 5. timeframe + timezone
  await page.tap('[data-testid=tf-M5]'); await until(async () => { const p = (await st())['pane:chart-0']; return p.lastBarTime % 300 === 0 && p.bars > 20 && (await st()).app.tf === 'M5' }, 8000, 'M5')
  check('M5 candles align to 5-minute UTC boundaries', ((await st())['pane:chart-0'].lastBarTime % 300) === 0)
  await page.tap('[data-testid=tf-H1]'); await until(async () => (await st())['pane:chart-0'].lastBarTime % 3600 === 0, 8000, 'H1')
  check('H1 candles align to the UTC hour', true)
  await page.tap('[data-testid=tf-M1]'); await until(async () => (await st())['pane:chart-0'].lastBarTime % 3600 !== 0 || true, 3000)
  await page.selectOption('[data-testid=tz]', 'UTC')
  const utcText = await page.locator('[data-testid=chart-time]').innerText()
  const cs = (await st()).app.cutoffMs
  const want = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(cs)
  check('timezone switch shows chart time in UTC', utcText.includes('UTC') && utcText.startsWith(want), utcText)
  await page.selectOption('[data-testid=tz]', 'America/New_York')
  check('and back to New York', (await page.locator('[data-testid=chart-time]').innerText()).includes('New York'))

  // ------------------------------------------------------------------ 6. layouts 1 / 2 / 4 on one cutoff
  for (const n of [2, 4, 1]) {
    await page.tap(`[data-testid=layout-${n}]`)
    await until(async () => { const x = await st(); return Array.from({ length: n }, (_, i) => x[`pane:chart-${i}`]?.bars > 50).every(Boolean) }, 10000, `${n} charts`)
    const panes = await page.locator('.charts .pane').count()
    const x = await st()
    check(`layout ${n}: ${n} chart(s) with data`, panes === n)
    check(`layout ${n}: no horizontal overflow`, x.app.scrollW <= x.app.innerW)
    if (n === 4) {
      const syms = new Set(Array.from({ length: 4 }, (_, i) => x[`pane:chart-${i}`].symbol))
      check('layout 4: four different pairs, all on the same delayed cutoff', syms.size === 4 && delayedSockets().length === 1, [...syms].join(','))
      await page.screenshot({ path: `${SHOTS}/2_four.png` })
    }
  }

  // ------------------------------------------------------------------ 7. labels overlay is OFF by default
  const labelReqs0 = requests.filter((r) => r.url.includes('/labels')).length
  s = await st()
  check('labels overlay is off by default (no /labels request, no label markers)', s.app.showLabels === false && labelReqs0 === 0 && s['pane:chart-0'].labelMarkers === 0)
  await page.tap('[data-testid=toggle-labels]')
  await until(async () => (await st())['pane:chart-0'].labelMarkers > 20, 15000, 'label markers')
  s = await st()
  check('labels toggle on: markers drawn on graded entries', s['pane:chart-0'].labelMarkers > 20, `${s['pane:chart-0'].labelMarkers} label markers`)
  const lreq = requests.filter((r) => r.url.includes('/labels'))
  check('label requests carry the chart delay (cannot ask past the delayed clock)', lreq.length > 0 && lreq.every((r) => /delay=300/.test(r.url)), lreq[0]?.url)
  await page.screenshot({ path: `${SHOTS}/3_labels.png` })
  await page.tap('[data-testid=ldir-sell]'); await sleep(400)
  check('sell direction switches markers', (await st())['pane:chart-0'].labelMarkers > 20)
  await page.tap('[data-testid=toggle-labels]'); await sleep(400)
  check('labels toggle off removes the label markers (practice-call markers stay)', (await st())['pane:chart-0'].labelMarkers === 0)

  // ------------------------------------------------------------------ 8. practice call (clean) graded as soon as final
  const before = (await api('/calls?limit=1000')).calls.length
  const callM0 = (await st())['pane:chart-0'].callMarkers
  await page.tap('[data-testid=buy]')
  const post = await until(() => requests.find((r) => r.method === 'POST' && r.url.endsWith('/calls')), 5000, 'POST /calls')
  check('call request has no client-chosen time (server stamps it)', !/t_ms|time/.test(post.body ?? ''), post.body)
  await until(async () => (await st()).app.calls === before + 1, 6000, 'call listed')
  const tCall = Date.now()
  await until(async () => (await st())['pane:chart-0'].callMarkers === callM0 + 1, 6000, 'call marker')
  check('a call marker appears on the chart', true)
  await page.tap('[data-testid=toggle-calls]')
  await until(async () => (await page.locator('[data-testid=call-row]').first().getAttribute('data-status')) === 'final', 25000, 'call graded')
  const dt = ((Date.now() - tCall) / 1000).toFixed(1)
  check('practice call shows its graded result once the outcome is final', true, `final ${dt} s after the call (outcome window = 5 min already elapsed on a 5 min delayed chart)`)
  const row = page.locator('[data-testid=call-row]').first()
  check('result row shows pips for 30s/60s/2m/5m', ((await row.locator('[data-testid=call-pnl]').innerText()).match(/[+-]?\d+\.\d/g) ?? []).length === 4)
  check('scorecard counts it as a clean, graded call', /(\d+) calls? · (\d+) graded/.test(await page.locator('[data-testid=score-clean-counts]').innerText()) && (await row.getAttribute('data-peeked')) === '0')
  const href = await page.locator('[data-testid=report-link]').getAttribute('href')
  const rep = await page.evaluate(async (h) => { const r = await fetch(h); return { status: r.status, ct: r.headers.get('content-type'), body: (await r.text()).slice(0, 4000) } }, href)
  check('calls panel links to the baseline / oracle / feature report, which loads', rep.status === 200 && rep.ct.startsWith('text/html') && /Baseline/.test(rep.body), `${href} -> ${rep.status}`)
  await page.screenshot({ path: `${SHOTS}/4_calls.png` })
  await page.tap('[data-testid=toggle-calls]')

  // ------------------------------------------------------------------ 9. peek: separate live stream, calls flagged
  const nBeforePeek = sockets.length
  await page.tap('[data-testid=toggle-peek]')
  await until(async () => (await st()).app.peekStatus === 'open' && (await st())['pane:peek']?.bars > 10, 15000, 'peek stream')
  s = await st()
  check('peek on: banner + live pane side by side', await page.locator('[data-testid=peek-banner]').isVisible() && await page.locator('[data-testid=peek-pane]').isVisible())
  check('peek chart is live while the main chart stays 5 min behind', Math.abs(Date.now() - s.app.peekCutoffMs) < 2000 && Math.abs(Date.now() - s.app.cutoffMs - 300000) < 2000,
    `peek ${(Date.now() - s.app.peekCutoffMs)} ms behind, main ${((Date.now() - s.app.cutoffMs) / 1000).toFixed(1)} s`)
  check('peek used its own socket (/ws/peek), delayed socket untouched', sockets.length === nBeforePeek + 1 && sockets.at(-1).url.includes('/ws/peek?') && sockets.at(-1).url.includes('peeked=1') && delayedSockets().length === 1)
  await page.screenshot({ path: `${SHOTS}/5_peek.png` })
  await page.tap('[data-testid=buy]')
  await until(async () => (await st()).app.calls === before + 2, 6000, 'peeked call listed')
  const peekedCall = (await api('/calls?limit=1')).calls[0]
  check('call made while Peek is on is stored with peeked = 1', peekedCall.peeked === 1, `id ${peekedCall.id}`)
  await page.tap('[data-testid=toggle-peek]')
  await until(async () => !(await st()).app.peekOn && sockets.at(-1).closed, 6000, 'peek closed')
  check('peek off: pane gone and its socket closed', (await page.locator('[data-testid=peek-pane]').count()) === 0 && sockets.at(-1).closed)
  await sleep(400)                      // a person needs far longer than this to move a thumb from Peek to Sell
  await page.tap('[data-testid=sell]')
  await until(async () => (await st()).app.calls === before + 3, 6000, 'clean call listed')
  check('call after peek is closed is clean again (peeked = 0)', (await api('/calls?limit=1')).calls[0].peeked === 0)

  // ------------------------------------------------------------------ 10. settings persist, but labels/peek never do
  await page.selectOption('[data-testid=tz]', 'UTC'); await page.tap('[data-testid=delay-60]'); await page.tap('[data-testid=toggle-labels]')
  await sleep(300); await page.reload()
  await until(async () => (await st()).app?.status === 'open', 20000, 'reload')
  s = await st()
  check('after reload: timezone and delay restored', s.app.tz === 'UTC' && s.app.delay === 60)
  check('after reload: labels overlay and peek are OFF again', s.app.showLabels === false && s.app.peekOn === false)
  await page.selectOption('[data-testid=tz]', 'America/New_York'); await page.tap('[data-testid=delay-300]')

  check('no console errors / uncaught exceptions during the whole run', errors.length === 0, errors.slice(0, 3).join(' | '))
} catch (e) {
  check('script ran to completion', false, e.message)
  await page.screenshot({ path: `${SHOTS}/failure.png` }).catch(() => {})
}

// ---------------------------------------------------------------------- 11. reconnect after the server dies
if (process.env.RECONNECT_DB) {
  const port = 8801
  const launch = () => spawn(process.env.LAB_PYTHON, ['-m', 'uvicorn', 'server.app:app', '--port', String(port), '--log-level', 'warning'],
    { cwd: root, env: { ...process.env, LAB_DB: process.env.RECONNECT_DB }, stdio: 'ignore' })
  let srv = launch()
  const p2 = await ctx.newPage()
  try {
    await sleep(2500)
    await openChartLab(p2, `http://127.0.0.1:${port}`)
    await p2.evaluate(() => { window.__noReload = 1 })
    const st2 = () => p2.evaluate(() => window.__lab.state())
    const until2 = async (fn, ms, what) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (await fn()) return true } catch { /* retry */ } await sleep(200) } throw new Error('timeout: ' + what) }
    await until2(async () => (await st2()).app?.status === 'open' && (await st2())['pane:chart-0'].bars > 50, 20000, 'p2 open')
    srv.kill('SIGKILL'); await sleep(300)
    await until2(async () => (await st2()).app.status !== 'open', 12000, 'detect drop')
    check('server killed: UI shows reconnecting (does not crash)', (await p2.locator('[data-testid=status]').innerText()).includes('reconnecting') || (await st2()).app.status !== 'open')
    await sleep(3000)
    srv = launch()
    // 'open' is reported the moment the socket connects; the fresh backfill follows right after, so wait for the
    // chart to be re-synced (cutoff back to now - delay) rather than trusting the stale pre-outage state
    await until2(async () => { const x = await st2(); return x.app.status === 'open' && x['pane:chart-0'].bars > 50 && Math.abs(Date.now() - x.app.cutoffMs - 300000) < 2500 }, 40000, 'p2 reconnect + resync')
    const a = (await st2()).app
    check('server back: UI reconnects by itself, same page, chart re-synced to the delay', await p2.evaluate(() => window.__noReload === 1), `lag ${((Date.now() - a.cutoffMs) / 1000).toFixed(1)} s`)
  } catch (e) { check('reconnect scenario', false, e.message) }
  srv.kill('SIGKILL')
}

await browser.close()
console.log(failed ? `\n${failed} CHECK(S) FAILED` : '\nALL E2E CHECKS PASSED')
process.exit(failed ? 1 : 0)
