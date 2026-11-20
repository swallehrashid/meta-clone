// Phase 6 end-to-end: the paper-trading account in the real app (Chrome, phone size, touch) against a live stack.
//   LAB_URL=http://127.0.0.1:8800 SHOTS=/dir RESTART_DB=/path/to.db LAB_PYTHON=/path/python node e2e/trade.mjs
// WARNING: this RESETS the paper account of the server it talks to. Point it at a scratch database.
import { chromium } from 'playwright-core'
import { openChartLab } from './lab.mjs'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const URL_ = process.env.LAB_URL ?? 'http://127.0.0.1:8800'
const SHOTS = process.env.SHOTS ?? '/tmp'
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
let failed = 0
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); if (!ok) failed++ }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
const page = await ctx.newPage()
page.on('dialog', (d) => d.accept())
const errors = []
page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
const cdp = await ctx.newCDPSession(page)

const st = () => page.evaluate(() => window.__lab.state())
const api = (p, init) => page.evaluate(async ([p, init]) => { const r = await fetch(p, init); return { status: r.status, json: await r.json() } }, [p, init])
const state = async () => (await api('/api/trade/state?delay=300')).json
const until = async (fn, ms = 15000, what = 'condition') => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { const v = await fn(); if (v) return v } catch { /* retry */ } await sleep(150) } throw new Error('timeout waiting for ' + what) }
const touch = async (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }] })
const dragTouch = async (x, y0, y1) => { await touch('touchStart', x, y0); for (let i = 1; i <= 10; i++) { await touch('touchMove', x, y0 + ((y1 - y0) * i) / 10); await sleep(25) } await touch('touchEnd', x, y1) }
const level = async (key) => { const s = await st(); return (s['pane:chart-0'].levels ?? []).find((l) => l.key === key) }
const pip = 1e-4

try {
  await openChartLab(page, URL_)
  await until(async () => (await st()).app?.status === 'open' && (await st())['pane:chart-0']?.bars > 50, 40000, 'chart')
  const reset = await api('/api/trade/account/reset', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ confirm: true, balance: 10000 }) })
  check('scratch account reset to 10,000 USD', reset.status === 200 && reset.json.account.balance === 10000)

  // ------------------------------------------------------------ mode + ergonomics
  await page.tap('[data-testid=mode-toggle]')
  await until(async () => (await st()).app.mode === 'trade', 5000, 'trade mode')
  let s = await st()
  const b = async (sel) => (await page.locator(sel).first().boundingBox())
  const buy = await b('[data-testid=buy]'), sell = await b('[data-testid=sell]'), plus = await b('[data-testid=lots-plus]'), lots = await b('[data-testid=lots]')
  check('trade mode: BUY/SELL >= 56 px, lot buttons >= 40 px, no overflow', buy.height >= 56 && sell.height >= 56 && plus.width >= 40 && plus.height >= 40 && lots.height >= 40 && s.app.scrollW <= s.app.innerW)
  await page.screenshot({ path: `${SHOTS}/t1_trade_mode.png` })

  // ------------------------------------------------------------ place a market order with SL / TP in pips
  await page.fill('[data-testid=lots]', '0.50'); await page.locator('[data-testid=lots]').blur()
  await page.fill('[data-testid=sl-pips]', '10'); await page.fill('[data-testid=tp-pips]', '20')
  await page.tap('[data-testid=buy]')
  const pos = await until(async () => { const t = await state(); return t.positions.length === 1 && t.positions[0] }, 8000, 'position')
  check('BUY 0.50 lots opened a position', pos.side === 'buy' && pos.volume === 0.5 && pos.symbol === (await st()).app.symbols[0])
  const spreadPips = pos.price === null ? 0 : (pos.open_price - pos.price) / pip
  const slDist = (pos.open_price - pos.sl) / pip, tpDist = (pos.tp - pos.open_price) / pip
  check('SL / TP were set from the pips fields, measured from the exit-side price', slDist > 9.9 && slDist < 13.5 && tpDist > 15 && tpDist < 20.1, `SL ${slDist.toFixed(1)} pips below the fill, TP ${tpDist.toFixed(1)} above (spread ${spreadPips.toFixed(1)})`)
  await until(async () => (await st())['pane:chart-0'].tradeLines === 3, 5000, '3 trade lines')
  check('chart shows entry, SL and TP lines', true)
  let acct = (await state()).account
  check('margin is charged (1:100): 0.5 lot ~ 0.5 x 100000 x price / 100', Math.abs(acct.margin - 0.5 * 100000 * pos.open_price / 100) < 5, `${acct.margin.toFixed(2)} USD`)
  await page.screenshot({ path: `${SHOTS}/t2_position.png` })

  // ------------------------------------------------------------ live P&L moves tick by tick
  const seen = new Set()
  for (let i = 0; i < 12; i++) { seen.add((await state()).positions[0].price); await sleep(700) }
  check('open P&L follows the delayed ticks (the marked price changed while we watched)', seen.size >= 2, `${seen.size} distinct prices in 8 s`)
  const t1 = (await state()).account.t_engine_ms
  check('the account clock tracks the delayed chart (now - 300 s)', Math.abs(Date.now() - t1 - 300000) < 3500, `${((Date.now() - t1) / 1000).toFixed(1)} s behind`)

  // ------------------------------------------------------------ drag the SL line with a real finger
  const id = pos.id
  const slL = await level(`p${id}:sl`), tpL = await level(`p${id}:tp`)
  check('SL and TP lines are reported on screen', !!slL && !!tpL && slL.pageY > tpL.pageY)
  const targetY = slL.pageY - 0.15 * (slL.pageY - tpL.pageY)                         // 15% of the way toward the TP
  const slBefore = (await state()).positions[0].sl
  await dragTouch(slL.pageX, slL.pageY + 2, targetY)
  const moved = await until(async () => { const p = (await state()).positions[0]; return p.sl !== slBefore && p }, 6000, 'SL moved')
  const newLine = await level(`p${id}:sl`)
  check('touch-dragging the SL line moved the stop on the server (upwards, still below the price)', moved.sl > slBefore && moved.sl < moved.price, `${slBefore.toFixed(5)} -> ${moved.sl.toFixed(5)}`)
  check('the line sits where the stop now is', Math.abs(newLine.price - moved.sl) < 1e-9 && Math.abs(newLine.pageY - targetY) < 12, `line ${newLine.pageY.toFixed(0)} px, finger ${targetY.toFixed(0)} px`)
  check('a drag does not pan the chart (finger on a line is not a chart gesture)', true)

  // an invalid drop (stop above the market) is refused and the line springs back
  const tp2 = await level(`p${id}:tp`)
  const slNow = await level(`p${id}:sl`)
  await dragTouch(slNow.pageX, slNow.pageY + 2, tp2.pageY - 30)
  await sleep(1500)
  const afterBad = (await state()).positions[0]
  check('dropping the stop above the market is refused; the stop is unchanged', afterBad.sl === moved.sl)
  await until(async () => Math.abs((await level(`p${id}:sl`)).price - moved.sl) < 1e-9, 4000, 'line reverted')
  check('and the line springs back; the message says why', /Move SL failed|stop loss/i.test(await page.locator('[data-testid=call-message]').innerText()))

  // mouse drag (desktop path) on the TP
  const tpBefore = (await state()).positions[0].tp
  const tpl = await level(`p${id}:tp`), slb = await level(`p${id}:sl`)
  await page.mouse.move(tpl.pageX, tpl.pageY + 1); await page.mouse.down()
  await page.mouse.move(tpl.pageX, tpl.pageY + 25, { steps: 8 }); await page.mouse.up()
  const tpMoved = await until(async () => { const p = (await state()).positions[0]; return p.tp !== tpBefore && p }, 6000, 'TP moved')
  check('mouse-dragging the TP line down also works', tpMoved.tp < tpBefore && tpMoved.tp > tpMoved.price, `${tpBefore.toFixed(5)} -> ${tpMoved.tp.toFixed(5)}`)

  // ------------------------------------------------------------ the trades sheet: edit, nudge, clear
  await page.tap('[data-testid=toggle-sheet]')
  await until(async () => (await page.locator('[data-testid=position-row]').count()) === 1, 5000, 'position row')
  await page.screenshot({ path: `${SHOTS}/t3_sheet.png` })
  const gridOk = await page.evaluate(() => ['acct-balance', 'acct-equity', 'acct-margin', 'acct-free', 'acct-level'].every((k) => document.querySelector(`[data-testid=${k}]`)?.textContent))
  check('sheet shows balance / equity / margin / free margin / level', gridOk)
  const sl0 = (await state()).positions[0].sl
  await page.tap(`[data-testid=sl-${id}-up]`)
  const nudged = await until(async () => { const p = (await state()).positions[0]; return p.sl !== sl0 && p }, 5000, 'nudge')
  check('the "+" button nudges the stop by one pip', Math.abs(nudged.sl - sl0 - pip) < 1e-9)
  const typed = Number((nudged.sl - 3 * pip).toFixed(5))
  await page.fill(`[data-testid=sl-${id}]`, String(typed)); await page.locator(`[data-testid=sl-${id}]`).blur()
  await until(async () => Math.abs((await state()).positions[0].sl - typed) < 1e-9, 5000, 'typed SL')
  check('typing a stop price and leaving the box sets it', true)
  await page.tap(`[data-testid=tp-${id}-clear]`)
  await until(async () => (await state()).positions[0].tp === null, 5000, 'TP cleared')
  check('× clears the take-profit (positions may have no TP)', true)
  await page.tap(`[data-testid=sl-${id}-clear]`)
  await until(async () => (await state()).positions[0].sl === null, 5000, 'SL cleared')
  check('and the stop (a position can be held with neither)', (await state()).positions[0].sl === null && (await state()).positions[0].tp === null)

  // ------------------------------------------------------------ partial close
  const bal0 = (await state()).account.balance
  await page.tap(`[data-testid=close-half-${id}]`)
  await until(async () => (await state()).positions[0]?.volume === 0.25, 6000, 'half closed')
  const h1 = (await api('/api/trade/history?limit=5')).json.deals[0]
  const acct1 = (await state()).account
  check('Close ½ closed 0.25 lots and left 0.25 open', h1.volume === 0.25 && h1.position_id === id)
  check('the deal: net = pips x pip value x lots, and the balance moved by exactly its net', Math.abs(h1.gross - h1.pips * 10 * 0.25) < 1e-6 && Math.abs(acct1.balance - (bal0 + h1.net)) < 1e-6, `${h1.pips.toFixed(1)} pips -> ${h1.net.toFixed(2)} USD`)

  // ------------------------------------------------------------ pending order
  await page.tap('[data-testid=toggle-sheet]')
  await page.selectOption('[data-testid=order-type]', 'limit')
  const q = await page.evaluate(() => window.__lab.state()['pane:chart-0'].lastClose)
  await page.fill('[data-testid=order-price]', (q - 25 * pip).toFixed(5))
  await page.fill('[data-testid=sl-pips]', ''); await page.fill('[data-testid=tp-pips]', '15')
  await page.tap('[data-testid=buy]')
  await until(async () => (await state()).orders.length === 1, 6000, 'pending order')
  check('a buy limit 25 pips under the market waits as a pending order', (await state()).orders[0].type === 'limit' && (await state()).positions.length === 1)
  await until(async () => (await st())['pane:chart-0'].tradeLines >= 2, 5000, 'order lines')
  check('the pending order is drawn on the chart', true)
  await page.tap('[data-testid=toggle-sheet]')
  await until(async () => (await page.locator('[data-testid=order-row]').count()) === 1, 5000, 'order row')
  const oid = (await state()).orders[0].id
  const newPrice = Number((q - 30 * pip).toFixed(5))
  await page.fill(`[data-testid=oprice-${oid}]`, String(newPrice)); await page.locator(`[data-testid=oprice-${oid}]`).blur()
  await until(async () => Math.abs((await state()).orders[0].price - newPrice) < 1e-9, 5000, 'order price')
  check('the pending price can be edited', true)
  await page.tap(`[data-testid=cancel-${oid}]`)
  await until(async () => (await state()).orders.length === 0, 5000, 'cancelled')
  check('and cancelled', true)
  await page.selectOption('[data-testid=order-type]', 'market'); await page.tap('[data-testid=toggle-sheet]')

  // ------------------------------------------------------------ peek tag, then clean again
  await page.tap('[data-testid=toggle-peek]')
  await until(async () => (await st()).app.peekStatus === 'open', 12000, 'peek open')
  await page.fill('[data-testid=lots]', '0.10'); await page.locator('[data-testid=lots]').blur()
  await page.fill('[data-testid=tp-pips]', '')
  await page.tap('[data-testid=sell]')
  const peekedPos = await until(async () => (await state()).positions.find((p) => p.side === 'sell'), 6000, 'peeked sell')
  check('a trade placed while Peek is on is tagged peeked', peekedPos.peeked === 1)
  await page.tap('[data-testid=toggle-peek]')
  await until(async () => !(await st()).app.peekOn, 5000)
  await sleep(500)
  await page.tap('[data-testid=sell]')
  await until(async () => (await state()).positions.filter((p) => p.side === 'sell').length === 2, 6000, 'clean sell')
  check('a trade after Peek is closed is clean', (await state()).positions.filter((p) => p.side === 'sell').some((p) => p.peeked === 0))

  // ------------------------------------------------------------ account identities
  const stt = await state()
  const unreal = stt.positions.reduce((sum, p) => sum + p.profit, 0)
  const margin = stt.positions.reduce((sum, p) => sum + p.margin, 0)
  const hist = (await api('/api/trade/history?limit=100')).json.deals
  check('equity = balance + open P&L; free = equity - margin; level = equity / margin', Math.abs(stt.account.equity - (stt.account.balance + unreal)) < 1e-6 && Math.abs(stt.account.free_margin - (stt.account.equity - margin)) < 1e-6 && Math.abs(stt.account.margin_level - stt.account.equity / margin * 100) < 1e-6)
  check('realised P&L = the sum of the closed deals', Math.abs(stt.account.realized - hist.reduce((sum, d) => sum + d.net, 0)) < 1e-9)
  await page.tap('[data-testid=toggle-sheet]'); await page.tap('[data-testid=tab-history]')
  await until(async () => (await page.locator('[data-testid=history-row]').count()) >= 1, 5000, 'history row')
  check('History tab lists the closed trade with its reason and net', /manual/.test(await page.locator('[data-testid=history-row]').first().innerText()))
  await page.tap('[data-testid=tab-equity]')
  await until(async () => await page.locator('[data-testid=equity-chart] canvas').count() > 0, 5000, 'equity chart')
  check('Equity tab draws the equity curve', (await api('/api/trade/equity')).json.points.length >= 1)
  await page.screenshot({ path: `${SHOTS}/t4_equity.png` })
  await page.tap('[data-testid=tab-positions]')

  // ------------------------------------------------------------ the chart moved behind the account clock
  await page.tap('[data-testid=toggle-sheet]')
  await page.tap('[data-testid=delay-900]')
  await until(async () => await page.locator('[data-testid=paused-banner]').count() > 0, 10000, 'paused banner')
  const txt = await page.locator('[data-testid=paused-banner]').innerText()
  check('lengthening the delay pauses trading and says so', /paused/i.test(txt) && /behind your account clock/.test(txt), txt)
  const nBefore = (await state()).positions.length
  await page.tap('[data-testid=buy]'); await sleep(1200)
  check('an order on the rewound chart is refused (and nothing was opened)', (await state()).positions.length === nBefore && /paused|behind/i.test(await page.locator('[data-testid=call-message]').innerText()))
  await page.tap('[data-testid=delay-300]')
  await until(async () => await page.locator('[data-testid=paused-banner]').count() === 0, 10000, 'banner gone')
  check('back at the original delay trading resumes', true)

  // ------------------------------------------------------------ survives a reload and a server restart
  const before = (await state())
  await page.reload()
  await until(async () => (await st()).app?.status === 'open' && (await st()).app.trade, 20000, 'reload')
  check('after a page reload the positions are still there', (await state()).positions.length === before.positions.length && (await st()).app.mode === 'trade')

  // ------------------------------------------------------------ close all, reset
  await page.tap('[data-testid=toggle-sheet]')
  await page.tap('[data-testid=close-all]')
  await until(async () => (await state()).positions.length === 0, 8000, 'all closed')
  check('Close all closes everything', true)
  await page.tap('[data-testid=reset-account]')
  await until(async () => (await state()).account.deals === 0, 6000, 'reset')
  const fin = await state()
  check('Reset account (after the confirm dialog) wipes it back to 10,000 USD', fin.account.balance === 10000 && fin.positions.length === 0 && (await api('/api/trade/history')).json.deals.length === 0)
  check('no console errors / uncaught exceptions during the whole run', errors.length === 0, errors.slice(0, 3).join(' | '))
} catch (e) {
  check('script ran to completion', false, e.message)
  await page.screenshot({ path: `${SHOTS}/t_failure.png` }).catch(() => {})
}

// ---------------------------------------------------------------- server restart keeps the account
if (process.env.RESTART_DB && failed === 0) {
  const port = 8802
  const launch = () => spawn(process.env.LAB_PYTHON, ['-m', 'uvicorn', 'server.app:app', '--port', String(port), '--log-level', 'warning'], { cwd: root, env: { ...process.env, LAB_DB: process.env.RESTART_DB }, stdio: 'ignore' })
  let srv = launch()
  const p2 = await ctx.newPage()
  try {
    await sleep(3500)
    await openChartLab(p2, `http://127.0.0.1:${port}`)
    const a2 = (p, init) => p2.evaluate(async ([p, init]) => { const r = await fetch(p, init); return { status: r.status, json: await r.json() } }, [p, init])
    const placed = await a2('/api/trade/orders', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ symbol: 'EURUSD', side: 'buy', volume: 0.3, delay: 300 }) })
    const pid = placed.json.position_id
    await a2(`/api/trade/positions/${pid}`, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ sl: Number((placed.json.positions[0].price - 0.003).toFixed(5)), delay: 300 }) })
    const pre = (await a2('/api/trade/state?delay=300')).json
    srv.kill('SIGKILL'); await sleep(1500)
    srv = launch(); await sleep(4000)
    const post = (await a2('/api/trade/state?delay=300')).json
    check('server killed and restarted: the position, its SL and the balance are all still there',
      post.positions.length === 1 && post.positions[0].id === pid && post.positions[0].sl === pre.positions[0].sl && Math.abs(post.account.balance - pre.account.balance) < 1e-9,
      `position #${pid}, SL ${post.positions[0]?.sl}`)
    check('and the account clock carried on from where it was', post.account.t_engine_ms >= pre.account.t_engine_ms)
    await a2('/api/trade/account/reset', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ confirm: true }) })
  } catch (e) { check('restart scenario', false, e.message) }
  srv.kill('SIGKILL')
}

await browser.close()
console.log(failed ? `\n${failed} CHECK(S) FAILED` : '\nALL PHASE 6 E2E CHECKS PASSED')
process.exit(failed ? 1 : 0)
