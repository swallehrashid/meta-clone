// Phase 7 end-to-end: run a bot on the delayed paper account through the real app (Chrome, phone size).
//   BOT_DB=/path/copy.db LAB_PYTHON=/path/python SHOTS=/dir node e2e/bot.mjs
// Starts its OWN collector (simulated market) + server on BOT_DB (use a COPY: it resets the paper account) so it can kill and
// restart the server alone to prove the bot resumes.
import { chromium } from 'playwright-core'
import { openChartLab } from './lab.mjs'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const DB = process.env.BOT_DB
const PY = process.env.LAB_PYTHON
const SHOTS = process.env.SHOTS ?? '/tmp'
const PORT = 8803
const URL_ = `http://127.0.0.1:${PORT}`
if (!DB || !PY) { console.error('set BOT_DB and LAB_PYTHON'); process.exit(2) }
let failed = 0
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); if (!ok) failed++ }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const procs = []
const launch = (args, env = {}) => { const p = spawn(PY, args, { cwd: root, env: { ...process.env, ...env }, stdio: 'ignore' }); procs.push(p); return p }
const server = () => launch(['-m', 'uvicorn', 'server.app:app', '--port', String(PORT), '--log-level', 'warning'], { LAB_DB: DB })
const waitHealth = async () => { for (let i = 0; i < 60; i++) { try { if ((await fetch(`${URL_}/health`)).ok) return true } catch { /* not yet */ } await sleep(500) } return false }

const collector = launch(['-m', 'collector.collector', '--sim', '--db', DB])
await sleep(8000)
let srv = server()
if (!(await waitHealth())) { console.error('server did not start'); procs.forEach((p) => p.kill('SIGKILL')); process.exit(2) }
await sleep(10000)                                                       // let the collector catch up to now

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
const page = await ctx.newPage()
page.on('dialog', (d) => d.accept())
const errors = []
page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
const st = () => page.evaluate(() => window.__lab.state())
const api = (p, init) => page.evaluate(async ([p, init]) => { const r = await fetch(p, init); return { status: r.status, json: await r.json() } }, [p, init])
const J = (body) => ({ method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })
const until = async (fn, ms = 20000, what = 'condition') => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = await fn(); if (v) return v } catch { /* retry */ } await sleep(250) } throw new Error('timeout waiting for ' + what) }

try {
  await openChartLab(page, URL_)
  await until(async () => (await st()).app?.status === 'open' && (await st())['pane:chart-0']?.bars > 30, 60000, 'chart')
  await api('/api/bot/stop', J({ close_positions: true }))
  await api('/api/trade/account/reset', J({ confirm: true, balance: 10000 }))

  // ------------------------------------------------------------ the Bot tab
  await page.tap('[data-testid=mode-toggle]')
  await page.tap('[data-testid=toggle-sheet]')
  await page.tap('[data-testid=tab-bot]')
  await until(async () => await page.locator('[data-testid=bot-form]').count() > 0, 8000, 'bot form')
  const opts = await page.locator('[data-testid=bot-select] option').allInnerTexts()
  check('the Bot tab lists the available bots and shows a parameter form generated from their schema', opts.includes('random_entry') && opts.includes('momentum_breakout') && (await page.locator('[data-testid=bp-p_enter]').count()) === 1, opts.join(', '))
  const s0 = await st()
  check('no horizontal overflow on the bot tab (390 px)', s0.app.scrollW <= s0.app.innerW)
  await page.screenshot({ path: `${SHOTS}/b1_form.png` })

  await page.fill('[data-testid=bp-p_enter]', '5')
  await page.tap('[data-testid=bot-start]')
  await until(async () => await page.locator('[data-testid=bot-error]').count() > 0, 4000, 'validation error')
  check('an invalid parameter is refused with a clear message and nothing starts', /p_enter.*at most 1/.test(await page.locator('[data-testid=bot-error]').innerText()) && !(await api('/api/bot')).json.running)

  // ------------------------------------------------------------ start a bot
  await page.fill('[data-testid=bp-p_enter]', '1'); await page.fill('[data-testid=bp-hold_s]', '60'); await page.fill('[data-testid=bp-volume]', '0.1')
  await page.tap('[data-testid=bot-start]')
  await until(async () => (await page.locator('[data-testid=bot-state]').count()) > 0 && /RUNNING/.test(await page.locator('[data-testid=bot-state]').innerText()), 8000, 'running')
  const info = (await api('/api/bot')).json
  check('the bot is running on the server with the parameters from the form', info.running && info.bot.name === 'random_entry' && info.bot.params.p_enter === 1 && info.bot.params.hold_s === 60 && info.bot.symbols.length === 1, JSON.stringify(info.bot.symbols))
  await page.screenshot({ path: `${SHOTS}/b2_running.png` })

  // a human trade placed while the bot runs must be left alone
  const human = (await api('/api/trade/orders', J({ symbol: info.bot.symbols[0] === 'GBPUSD' ? 'EURUSD' : 'GBPUSD', side: 'buy', volume: 0.1, delay: 300 }))).json.position_id

  // ------------------------------------------------------------ it trades on its own, on the delayed clock
  const botPos = await until(async () => { const t = (await api('/api/trade/state?delay=300')).json; return t.positions.find((p) => p.source === 'bot') }, 150000, 'a bot position')
  check('the bot opened a position by itself (tagged bot, clean)', botPos.source === 'bot' && botPos.peeked === 0, `#${botPos.id} ${botPos.side} ${botPos.volume} ${botPos.symbol}`)
  const state = (await api('/api/trade/state?delay=300')).json
  const chartT = state.account.t_engine_ms
  check('it acted on the DELAYED clock (the position opened ~5 minutes in the past)', Math.abs(Date.now() - botPos.open_ms - 300000) < 130000, `${((Date.now() - botPos.open_ms) / 1000).toFixed(0)} s ago`)
  await page.tap('[data-testid=tab-positions]')
  await until(async () => await page.locator('[data-testid=bot-tag]').count() > 0, 6000, 'bot tag')
  check('the position row carries a "bot" badge', true)
  await page.tap('[data-testid=tab-bot]')
  await until(async () => (await page.locator('[data-testid=bot-log] [data-kind=open]').count()) >= 1, 6000, 'log row')
  check('the activity log shows what it did', (await page.locator('[data-testid=bot-stats]').innerText()).includes('decisions'))
  await page.screenshot({ path: `${SHOTS}/b3_log.png` })

  // ------------------------------------------------------------ closes on its timer; never exceeds its limits; ignores the human's trade
  const deal = await until(async () => (await api('/api/trade/history?limit=50')).json.deals.find((d) => d.source === 'bot'), 150000, 'a closed bot trade')
  check('it closed the trade itself after hold_s (a manual-reason exit from the bot, not a stop)', deal.reason === 'manual' && deal.duration_s >= 55 && deal.duration_s < 240, `${deal.duration_s.toFixed(0)} s`)
  const mine = (await api('/api/trade/state?delay=300')).json.positions
  check('the human\'s trade was never touched by the bot', mine.some((p) => p.id === human && p.source === 'manual'))
  check('open bot positions never exceeded the risk limit (<= 3)', mine.filter((p) => p.source === 'bot').length <= 3)

  // ------------------------------------------------------------ peek does not contaminate a bot
  await page.tap('[data-testid=toggle-sheet]')
  await page.tap('[data-testid=toggle-peek]')
  await until(async () => (await st()).app.peekStatus === 'open', 15000, 'peek')
  await sleep(100000)                                                  // let the bot take a couple more decisions while Peek is ON
  const deals = (await api('/api/trade/history?limit=100')).json.deals.filter((d) => d.source === 'bot')
  check('trades the bot took while Peek was on are still clean (peeked = 0)', deals.length >= 2 && deals.every((d) => d.peeked === 0), `${deals.length} bot trades`)
  await page.tap('[data-testid=toggle-peek]')

  // ------------------------------------------------------------ stop: its trades only
  await page.tap('[data-testid=toggle-sheet]'); await page.tap('[data-testid=tab-bot]')
  await page.tap('[data-testid=bot-stop-close]')
  await until(async () => !(await api('/api/bot')).json.running, 8000, 'stopped')
  const after = (await api('/api/trade/state?delay=300')).json
  check('"Stop & close its trades" stops the bot, closes only ITS positions, and the form is back', after.positions.every((p) => p.source === 'manual') && after.positions.some((p) => p.id === human) && await page.locator('[data-testid=bot-form]').count() > 0)

  // ------------------------------------------------------------ it comes back after a server restart
  await page.fill('[data-testid=bp-p_enter]', '0.5'); await page.fill('[data-testid=bp-hold_s]', '90')
  await page.tap('[data-testid=bot-start]')
  await until(async () => (await api('/api/bot')).json.running, 8000, 'running again')
  srv.kill('SIGKILL'); await sleep(1500)
  srv = server()
  check('server restarted', await waitHealth())
  const r = await until(async () => { const j = (await api('/api/bot')).json; return j.running && j }, 15000, 'bot resumed')
  check('after a hard server restart the bot is running again with the same parameters', r.bot.name === 'random_entry' && r.bot.params.p_enter === 0.5 && r.bot.params.hold_s === 90)
  await page.reload()
  await until(async () => (await st()).app?.status === 'open', 40000, 'reload')
  check('and the page shows it as running', (await st()).app.bot.running === true)
  await api('/api/bot/stop', J({ close_positions: true }))
  const real = errors.filter((e) => !/WebSocket connection to .* failed/.test(e))      // the browser logs this while we deliberately kill the server
  check('no console errors / uncaught exceptions during the whole run (a refused websocket during the deliberate server kill is expected)', real.length === 0, real.slice(0, 3).join(' | '))
} catch (e) {
  check('script ran to completion', false, e.message)
  await page.screenshot({ path: `${SHOTS}/b_failure.png` }).catch(() => {})
}
await browser.close()
procs.forEach((p) => p.kill('SIGKILL'))
console.log(failed ? `\n${failed} CHECK(S) FAILED` : '\nALL PHASE 7 E2E CHECKS PASSED')
process.exit(failed ? 1 : 0)
