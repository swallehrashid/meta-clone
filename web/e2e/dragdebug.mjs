import { chromium } from 'playwright-core'
const URL_ = process.env.LAB_URL
const b = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
const page = await ctx.newPage(); const cdp = await ctx.newCDPSession(page)
await page.goto(URL_)
await page.waitForFunction(() => window.__lab?.state().app?.status === 'open' && window.__lab.state()['pane:chart-0']?.bars > 50, null, { timeout: 40000 })
await page.evaluate(async () => { await fetch('/api/trade/account/reset', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ confirm: true }) }) })
const r = await page.evaluate(async () => (await (await fetch('/api/trade/orders', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ symbol: 'EURUSD', side: 'buy', volume: 0.5, delay: 300, sl: null }) })).json()))
const bid = r.positions[0].price
await page.evaluate(async ([id, sl, tp]) => { await fetch('/api/trade/positions/' + id, { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ sl, tp, delay: 300 }) }) }, [r.position_id, +(bid - 0.0010).toFixed(5), +(bid + 0.0020).toFixed(5)])
await page.waitForFunction(() => (window.__lab.state()['pane:chart-0'].levels ?? []).length >= 2, null, { timeout: 8000 })
await page.evaluate(() => {
  window.__ev = []
  const el = document.querySelector('[data-testid=chart-0] .chart')
  for (const t of ['pointerdown', 'pointermove', 'pointerup', 'pointercancel', 'touchstart', 'touchmove', 'touchend', 'mousedown', 'mouseup'])
    el.addEventListener(t, (e) => window.__ev.push(t + (e.pointerType ? ':' + e.pointerType : '') + (e.defaultPrevented ? '!' : '')), true)
})
const lv = await page.evaluate(() => window.__lab.state()['pane:chart-0'].levels)
console.log('levels', JSON.stringify(lv))
const sl = lv.find((l) => l.key.endsWith(':sl'))
console.log('touch-action on chart box:', await page.evaluate(() => getComputedStyle(document.querySelector('[data-testid=chart-0] .chart')).touchAction))
const send = (type, x, y) => cdp.send('Input.dispatchTouchEvent', { type, touchPoints: type === 'touchEnd' ? [] : [{ x, y, id: 1 }] })
await send('touchStart', sl.pageX, sl.pageY + 2)
for (let i = 1; i <= 8; i++) { await send('touchMove', sl.pageX, sl.pageY + 2 - i * 4); await new Promise((r) => setTimeout(r, 30)) }
await send('touchEnd', sl.pageX, sl.pageY - 30)
await new Promise((r) => setTimeout(r, 1200))
const ev = await page.evaluate(() => window.__ev)
const counts = {}; ev.forEach((e) => (counts[e] = (counts[e] ?? 0) + 1))
console.log('events seen by the chart box:', JSON.stringify(counts))
console.log('sl before', +(bid - 0.0010).toFixed(5), 'after', (await page.evaluate(async () => (await (await fetch('/api/trade/state?delay=300')).json()).positions[0].sl)))
await b.close()
