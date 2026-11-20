// End-to-end check of the MT5-style terminal (`/`) in real Chrome at the size of the reference screenshots (360 x 800, DPR 2).
//   LAB_ADMIN_PASSWORD=... LAB_URL=http://127.0.0.1:8800 SHOTS=/dir node e2e/terminal.mjs
// Part A taps EVERY button on every screen, one at a time from a fresh screen, and fails if any of them does nothing visible.
// Part B walks the trading workflows (one click, pending orders incl. stop limit, SL/TP on the chart, close, history, report, settings).
// RESETS the server's paper account.
import { chromium } from 'playwright-core'

const URL_ = process.env.LAB_URL ?? 'http://127.0.0.1:8800'
const PW = process.env.LAB_ADMIN_PASSWORD
const SHOTS = process.env.SHOTS ?? '/tmp'
if (!PW) { console.error('set LAB_ADMIN_PASSWORD'); process.exit(2) }
let failed = 0
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); if (!ok) failed++ }
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const tid = (id) => `[data-testid=${id}]`

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await browser.newContext({ viewport: { width: 360, height: 800 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
await ctx.addInitScript(() => {
  window.__opened = []; window.__printed = 0; window.__downloads = 0
  window.open = (u) => { window.__opened.push(String(u)); return null }
  window.print = () => { window.__printed++ }
  const click = HTMLAnchorElement.prototype.click
  HTMLAnchorElement.prototype.click = function () { if (this.download) { window.__downloads++; return } return click.call(this) }
})
const page = await ctx.newPage()
const errors = []
page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource|fonts\.g/.test(m.text())) errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
const st = () => page.evaluate(() => window.__lab.state())
const term = async () => (await st()).terminal
const until = async (fn, ms = 20000, what = 'condition') => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) { try { const v = await fn(); if (v) return v } catch { /* retry */ } await sleep(150) }
  throw new Error('timeout waiting for ' + what)
}
const api = async (path, init) => (await ctx.request.fetch(URL_ + path, init)).json()
await ctx.request.post(`${URL_}/api/admin/login`, { data: { username: 'admin', password: PW } })
const original = await api('/api/admin/terminal')
await ctx.request.put(`${URL_}/api/admin/terminal`, { data: { delay_s: 300, tz: 'America/New_York', tf: 'M1', volume: 0.1 } })
await ctx.request.post(`${URL_}/api/trade/account/reset`, { data: { confirm: true } })

/** What a person sees, minus everything that moves on its own (prices, clocks, countdowns, positions of lines). */
const signature = () => page.evaluate(() => {
  const root = document.querySelector('.mt5')
  if (!root) return 'none'
  const html = root.innerHTML
    .replace(/<canvas[^>]*>/g, '').replace(/style="[^"]*"/g, '').replace(/class="(px|ocb|pill|lasttag|q|val|pl|up|dn)( [^"]*)?"/g, '')
    .replace(/\d/g, '').replace(/data-expanded="."/g, '')
  // input values live in properties, not in the HTML: a stepper that changes a field must count
  const inputs = [...root.querySelectorAll('input')].map((i) => (i.type === 'checkbox' ? String(i.checked) : i.value)).join(',')
  return html + '|' + inputs + '|' + window.__opened.length + '|' + window.__printed + '|' + window.__downloads + '|' + location.hash
})

const PREFS = { oneClick: true, oneClickPanel: true }
const SCREENS = ['quotes', 'chart', 'trade', 'history', 'messages', 'settings', 'news', 'mailbox', 'journal', 'calendar', 'community', 'algo', 'guide', 'about']
const openScreen = async (tab, extra = {}) => {
  await page.evaluate(([tab, prefs]) => {
    sessionStorage.setItem('mt5.tab', tab)
    // every step starts from the default settings (a swept Settings toggle must not leak into the next screen); only the
    // device's own identity (MetaQuotes ID) and the user's symbol choices are kept
    const cur = JSON.parse(localStorage.getItem('mt5.prefs.v1') || '{}')
    localStorage.setItem('mt5.prefs.v1', JSON.stringify({ mqid: cur.mqid, hidden: [], order: cur.order ?? [], ...prefs }))
  }, [tab, { ...PREFS, ...extra }])
  await page.reload()
  await page.waitForSelector('[data-testid=terminal]')
  await until(async () => (await term())?.tab === tab, 15000, 'screen ' + tab)
  if (tab === 'chart') await until(async () => (await term()).bars > 5, 30000, 'chart data')
  if (tab === 'quotes') await until(async () => (await page.$$('[data-testid=quotes] .q, [data-testid=quotes] .qs')).length > 0, 15000, 'quotes')
  await sleep(600)
}

try {
  await page.goto(URL_)
  await page.waitForSelector('[data-testid=terminal]', { timeout: 30000 })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })

  // =========================================================================================== A. no dead buttons
  // open a position and a pending order first, so the Trade / History screens have rows (and their buttons) too
  await openScreen('chart')
  await page.click(tid('oc-buy')); await until(async () => (await term()).positions === 1, 8000, 'position')
  let total = 0
  const dead = []
  for (const tab of SCREENS) {
    await openScreen(tab)
    // buttons plus the rows / chips / labels MT5 lets you tap; the selected tab is skipped (tapping it does nothing in MT5 either)
    const SEL = '.mt5 button:not([disabled]), .mt5 .q, .mt5 .qs, .mt5 .row, .mt5 .chip, .mt5 .ctitle .s'
    const tappable = () => page.$$eval(SEL, (els) => els.map((x, i) => ({ i, ok: !!x.offsetParent && !x.closest('nav.nav') && !(x.closest('.tabs') && x.classList.contains('on')) }))
      .filter((x) => x.ok).map((x) => x.i))
    const n = (await tappable()).length
    // two snapshots without touching anything: the comparison must be stable, or the sweep would prove nothing
    const s1 = await signature(); await sleep(700); const s2 = await signature()
    check(`[${tab}] the screen is still when nobody touches it (sweep is meaningful)`, s1 === s2)
    for (let i = 0; i < n; i++) {
      if (i > 0) await openScreen(tab)
      const idx = (await tappable())[i]
      if (idx === undefined) continue
      const b = (await page.$$(SEL))[idx]
      const name = await b.evaluate((x) => x.getAttribute('data-testid') || x.getAttribute('aria-label') || x.getAttribute('data-symbol') || x.textContent.trim().slice(0, 30) || x.className)
      const before = await signature()
      await b.click({ timeout: 5000 }).catch(() => {})
      await sleep(450)
      const after = await signature()
      total++
      if (before === after) dead.push(`${tab}: ${name}`)
    }
  }
  check(`every button on every screen does something (${total} buttons tapped)`, dead.length === 0, dead.join(' | '))

  // the pages behind the screens, and the order panel, swept the same way
  const PAGES = [
    ['indicators', 'chart', [tid('indicators')]],
    ['indicator list', 'chart', [tid('indicators'), tid('ind-add-main')]],
    ['indicator parameters', 'chart', [tid('indicators'), tid('ind-add-main'), tid('pick-ma')]],
    ['chart settings', 'chart', [tid('tf'), tid('tf-gear')]],
    ['order panel', 'chart', [tid('order-ring'), tid('op-grab')]],
    ['order panel (pending)', 'chart', [tid('order-ring'), tid('op-type-sell_stop'), tid('op-grab')]],
    ['add symbol', 'quotes', [tid('q-add')]],
    ['selected symbols', 'quotes', [tid('q-edit')]],
    ['report', 'history', [tid('h-report')]],
    ['accounts', 'quotes', [tid('menu'), tid('drawer-manage')]],
    ['otp', 'settings', [tid('set-otp')]],
    ['mail', 'mailbox', [tid('mail-0')]],
  ]
  const PSEL = '.mt5 .page button:not([disabled]), .mt5 .op button:not([disabled]), .mt5 .page .li'
  const ptappable = () => page.$$eval(PSEL, (els) => els.map((x, i) => ({ i, ok: !!x.offsetParent && !(x.closest('.rtabs') && x.classList.contains('on')) && !x.classList.contains('on') }))
    .filter((x) => x.ok).map((x) => x.i))
  let ptotal = 0
  const pdead = []
  for (const [name, tab, path] of PAGES) {
    const go = async () => { await openScreen(tab, { oneClick: false, oneClickPanel: false }); for (const s of path) { await page.click(s, { timeout: 8000 }); await sleep(250) } await sleep(300) }
    await go()
    const n = (await ptappable()).length
    for (let i = 0; i < n; i++) {
      if (i > 0) await go()
      const idx = (await ptappable())[i]
      if (idx === undefined) continue
      const b = (await page.$$(PSEL))[idx]
      const label = await b.evaluate((x) => x.getAttribute('data-testid') || x.getAttribute('aria-label') || x.textContent.trim().slice(0, 30) || x.className)
      if (/^(home|)$/.test(label.trim()) && !(await b.evaluate((x) => x.tagName === 'BUTTON'))) continue   // the folder breadcrumb (home) row is not a button in MT5 either
      const before = await signature()
      await b.click({ timeout: 5000 }).catch(() => {})
      await sleep(450)
      ptotal++
      if (before === (await signature())) pdead.push(`${name}: ${label}`)
    }
  }
  check(`every button on every page and in the order panel does something (${ptotal} tapped)`, pdead.length === 0, pdead.join(' | '))

  // =========================================================================================== B. workflows
  await ctx.request.post(`${URL_}/api/trade/account/reset`, { data: { confirm: true } })
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear() })
  await openScreen('quotes', { oneClick: false, oneClickPanel: false })
  check('the font is Roboto (standard MT5 font), never the phone\'s own', /^Roboto/.test((await term()).font))
  check('Pure black theme by default', (await term()).theme === 'black')
  check('no horizontal overflow at 360 px', (await term()).scrollW <= (await term()).innerW)
  await page.screenshot({ path: `${SHOTS}/t-quotes.png` })

  // quotes: + add symbol / folders / hide and show a symbol; pencil reorder; tap menu
  await page.click(tid('q-add')); await page.waitForSelector(tid('add-symbol'))
  await page.click(tid('folder-Forex')); await page.waitForSelector(tid('folder-page'))
  const first = (await term()).symbol
  await page.click(tid(`sym-${first}`))
  await page.click(tid('back')); await page.click(tid('back'))
  check('a symbol removed in Add symbol disappears from Quotes', !(await page.$(`[data-testid=quotes] [data-symbol=${first}]`)))
  await page.click(tid('q-add')); await page.fill(tid('symbol-search'), first.slice(0, 3)); await page.click(tid(`sym-${first}`)); await page.click(tid('back'))
  check('…and comes back when added again (search)', !!(await page.$(`[data-testid=quotes] [data-symbol=${first}]`)))
  await page.click(tid('q-edit')); await page.waitForSelector(tid('selected-symbols'))
  const order0 = await page.$$eval('[data-testid^=sel-] b', (e) => e.map((x) => x.textContent))
  const h = await page.locator(tid(`drag-${order0[0]}`)).boundingBox()
  await page.mouse.move(h.x + 10, h.y + 10); await page.mouse.down(); await page.mouse.move(h.x + 10, h.y + 10 + 58 * 2, { steps: 6 }); await page.mouse.up()
  const order1 = await page.$$eval('[data-testid^=sel-] b', (e) => e.map((x) => x.textContent))
  check('Selected symbols: dragging the handle reorders', order1[2] === order0[0] && order1[0] === order0[1], order1.join(','))
  await page.click(tid('back'))
  const qorder = await page.$$eval('[data-testid=quotes] .q', (e) => e.map((x) => x.dataset.symbol))
  check('…and Quotes follows the new order', qorder.join() === order1.join(), qorder.join())
  await page.click('[data-testid=quotes] .q'); await page.click(tid('qm-props')); await page.waitForSelector(tid('properties'))
  check('quote menu → Properties shows the specification', /Contract size/.test(await page.innerText(tid('properties')))); await page.click(tid('back'))
  await page.click('[data-testid=quotes] .q'); await page.click(tid('qm-mode'))
  check('quote menu → Simple view mode', (await page.$$('.qs')).length > 0); await page.click('.qs'); await page.click(tid('qm-mode'))

  // chart: timeframes incl. W1/MN, crosshair icon swap, one click disclaimer + trades
  await page.click('[data-testid=quotes] .q'); await page.click(tid('qm-chart'))
  await until(async () => (await term()).tab === 'chart' && (await term()).bars > 5, 30000, 'chart')
  await page.click(tid('tf')); await page.waitForSelector(tid('tf-bar'))
  check('timeframe bar: M1 M5 M15 M30 H1 H4 D1 W1 MN + settings', (await page.$$eval('[data-testid=tf-bar] button', (b) => b.map((x) => x.textContent).join(' '))).startsWith('M1 M5 M15 M30 H1 H4 D1 W1 MN'))
  await page.click(tid('tf-D1')); await until(async () => (await st()).chart.tf === 'D1' && (await st()).chart.bars > 0, 30000, 'D1')
  const d1 = (await st()).chart.bars
  await page.click(tid('tf')); await page.click(tid('tf-W1')); await until(async () => (await st()).chart.tf === 'W1', 10000, 'W1')
  await sleep(800)
  check('W1 is built from D1 (calendar weeks)', (await st()).chart.bars >= 1 && (await st()).chart.bars <= d1, `${(await st()).chart.bars} weeks from ${d1} days`)
  await page.click(tid('tf')); await page.click(tid('tf-M1')); await until(async () => (await st()).chart.tf === 'M1' && (await st()).chart.bars > 5, 30000, 'M1')
  await page.click(tid('crosshair'))
  check('crosshair button: crosshair on, icon becomes the move icon', (await st()).chart.crosshair === true)
  await page.click(tid('crosshair'))
  await page.click(tid('oneclick-toggle')); await page.waitForSelector(tid('oneclick-dialog'))
  check('One Click Trading asks for acceptance first (like MT5)', true)
  await page.click(tid('oneclick-accept')); await page.waitForSelector(tid('oneclick'))
  await page.click(tid('vol-up')); await page.waitForSelector(tid('vol-dropdown'))
  check('volume ⌃ opens +0.01 / +0.1 / +0.5 and larger presets', /\+0\.01[\s\S]*\+0\.5/.test(await page.innerText(tid('vol-dropdown'))))
  await page.click('[data-testid=vol-dropdown] .inc button >> nth=0')
  check('…+0.01 adds to the volume', (await page.innerText(tid('oc-volume'))) === '0.11')
  await page.click(tid('oc-volume')); await page.fill(tid('vol-input'), '0.2'); await page.click(tid('vol-ok'))
  check('tapping the volume lets you type it', (await page.innerText(tid('oc-volume'))) === '0.2')
  await page.click(tid('oc-sell'))
  const toast = await until(() => page.textContent(tid('toast')), 8000, 'toast')
  check('SELL fills at once and says so', /^sell 0\.2 [A-Z]{6} at \d+\.\d+ — done$/.test(toast), toast)
  await until(async () => (await term()).positions === 1, 8000, 'position')
  check('the position is drawn on the chart', (await st()).chart.levels.some((l) => l.key.startsWith('p')))

  // order panel: Buy Stop Limit with expiration Today + SL/TP, placed from the expanded panel
  await page.click(tid('order-ring')); await page.waitForSelector(tid('order-panel'))
  await page.click(tid('op-type-buy_stop_limit'))
  check('collapsed panel shows the order line and the stop limit line on the chart', (await st()).chart.levels.some((l) => l.key === 'dprice') && (await st()).chart.levels.some((l) => l.key === 'dstoplimit'))
  await page.click(tid('op-grab')); await until(async () => (await page.getAttribute(tid('order-panel'), 'data-expanded')) === '1', 5000, 'expanded')
  await page.click(tid('op-addstops'))
  await page.click(tid('op-expiration')); await page.click('text=Today')
  await page.click(tid('op-comment')); await page.fill(tid('op-comment-input'), 'e2e')
  const margin = await page.innerText('.op .mrg')
  check('Margin ≈ … USD and Free: … USD are shown', /Margin ≈ [\d\s., ]+ USD/.test(margin) && /Free: [\d\s., ]+ USD/.test(margin), margin)
  await page.click(tid('op-place'))
  await until(async () => (await term()).orders === 1, 8000, 'stop limit order')
  const ord = (await api('/api/trade/state?delay=300')).orders[0]
  check('Buy Stop Limit reaches the account with its stop limit price, SL, TP, comment and today\'s expiry',
    ord.type === 'stop_limit' && ord.stoplimit < ord.price && ord.sl !== null && ord.tp !== null && ord.comment === 'e2e' && ord.expire_ms > ord.placed_ms && ord.expire_ms - ord.placed_ms <= 86400_000,
    JSON.stringify({ t: ord.type, p: ord.price, sl2: ord.stoplimit, exp: ord.expire_ms - ord.placed_ms }))
  if (await page.$(tid('order-panel'))) await page.click(tid('order-ring'))

  // modify the position from the Trade tab, then drag its SL on the chart (confirmation dialog), close it
  await page.click(tid('nav-trade')); await page.waitForSelector('[data-testid^=pos-]')
  const holdOn = async (sel) => { const bx = await page.locator(sel).first().boundingBox(); await page.mouse.move(bx.x + 80, bx.y + 15); await page.mouse.down(); await sleep(650); await page.mouse.up() }
  await page.click('[data-testid^=pos-]'); check('tapping a position shows its details', !!(await page.$('[data-testid^=det-]')))
  await holdOn('[data-testid^=pos-]'); await page.waitForSelector(tid('trade-menu'))
  check('press and hold: Close / Modify / Trade / Chart / Bulk Operations', /Close position[\s\S]*Modify position[\s\S]*Trade[\s\S]*Chart[\s\S]*Bulk Operations/.test(await page.innerText(tid('trade-menu'))))
  await page.click(tid('pm-modify')); await page.waitForSelector(tid('op-modify'))
  const pos = (await api('/api/trade/state?delay=300')).positions[0]
  const pt = pos.symbol.includes('JPY') ? 0.001 : 0.00001
  await page.fill(tid('op-sl-field'), (pos.open_price + 300 * pt).toFixed(pos.symbol.includes('JPY') ? 3 : 5))
  await page.fill(tid('op-tp-field'), (pos.open_price - 300 * pt).toFixed(pos.symbol.includes('JPY') ? 3 : 5))
  await page.click(tid('op-modify'))
  await until(async () => (await api('/api/trade/state?delay=300')).positions[0].sl !== null, 8000, 'modified')
  check('Modify position sets SL and TP on the account', true)
  await page.click(tid('nav-chart')); await until(async () => (await st()).chart.levels.some((l) => /sl$/.test(l.key)), 10000, 'SL line')
  const slKey = (await st()).chart.levels.find((l) => /sl$/.test(l.key)).key
  const hd = await page.locator(tid(`handle-${slKey}`)).boundingBox()
  await page.mouse.move(hd.x + 10, hd.y + 10); await page.mouse.down(); await page.mouse.move(hd.x + 10, hd.y + 40, { steps: 5 }); await page.mouse.up()
  await page.waitForSelector(tid('dialog')); await page.click(tid('confirm-ok'))
  await until(async () => Math.abs((await api('/api/trade/state?delay=300')).positions[0].sl - pos.sl) > 0 || (await api('/api/trade/state?delay=300')).positions[0].sl !== (pos.open_price + 300 * pt), 8000, 'dragged SL')
  check('dragging the SL line on the chart (after the confirmation) moves the stop', true)
  await page.click(tid('nav-trade')); await holdOn('[data-testid^=pos-]'); await page.click(tid('pm-close'))
  await page.waitForSelector(tid('op-close'))
  check('Close position: MT5\'s yellow "Close #… with profit/loss" button', /^Close #\d+ sell 0\.2 [A-Z]{6} at [\d.]+ with (profit|loss) /.test(await page.innerText(tid('op-close'))))
  await page.click(tid('op-close')); await until(async () => (await term()).positions === 0, 8000, 'closed')
  await holdOn('[data-testid^=ord-]'); await page.click(tid('om-delete')); await until(async () => (await term()).orders === 0, 8000, 'deleted')
  check('pending order: press and hold → Delete order', true)

  // history: tabs, sort, period, symbol chip, $ toggle, report
  await page.click(tid('nav-history')); await until(async () => /sell 0\.2/.test(await page.innerText(tid('history'))), 10000, 'history row')
  check('History/POSITIONS: summary + the closed trade + the deposit', /Profit:[\s\S]*Deposit:[\s\S]*Swap:[\s\S]*Commission:[\s\S]*Balance:/.test(await page.innerText(tid('history'))))
  await page.click(tid('htab-orders'))
  check('History/ORDERS: filled + cancelled orders with a Filled / Canceled / Total summary', /Filled:[\s\S]*Canceled:[\s\S]*Total:/.test(await page.innerText(tid('history'))) && /cancelled/.test(await page.innerText(tid('history'))))
  await page.click(tid('htab-deals')); check('History/DEALS: in and out deals', /in[\s\S]*out|out[\s\S]*in/.test(await page.innerText(tid('history'))))
  await page.click(tid('h-period')); check('period menu: Today / Last week / Last month / Last 3 months / Custom period', /Today[\s\S]*Last week[\s\S]*Last month[\s\S]*Last 3 months[\s\S]*Custom period/.test(await page.innerText(tid('history-menu'))))
  await page.click(tid('period-custom')); await page.waitForSelector(tid('custom-period')); await page.click(tid('period-ok'))
  await page.click(tid('h-money')); check('$ icon: profit in points', /pts/.test(await page.innerText(tid('history'))))
  await page.click(tid('h-money'))
  await page.click(tid('h-report')); await page.waitForSelector(tid('report'))
  for (const r of ['summary', 'pl', 'ls', 'symbols', 'risks']) { await page.click(tid(`rtab-${r}`)); check(`Report → ${r}`, !!(await page.$(tid(`report-${r}`)))) }
  await page.click(tid('r-download')); check('Report download saves a file', (await page.evaluate(() => window.__downloads)) > 0)
  await page.click(tid('r-print')); check('Report print opens printing', (await page.evaluate(() => window.__printed)) > 0)
  await page.click(tid('back'))

  // settings change behaviour, not just a tick
  await page.click(tid('menu')); await page.click(tid('drawer-settings'))
  await page.click(tid('set-theme')); await page.click('.menu >> text=Light')
  check('Choose theme → Light', (await term()).theme === 'light')
  await page.click(tid('set-theme')); await page.click('.menu >> text=Pure black')
  await page.click(tid('set-accountInMenu')); check('Account info in bottom menu off → "Trade" label instead of the balance pill', !(await page.$(tid('equity-pill'))) && /Trade/.test(await page.innerText(tid('nav-trade'))))
  await page.click(tid('set-accountInMenu'))
  await page.click(tid('set-lock')); await page.fill(tid('pin-input'), '2468'); await page.click(tid('pin-ok')); await page.fill(tid('pin-input'), '2468'); await page.click(tid('pin-ok'))
  await page.reload(); await page.waitForSelector(tid('lock'))
  check('Lock Screen: the app asks for the PIN on start', true)
  for (const d of '2468') await page.click(tid(`pin-${d}`))
  await until(async () => !(await page.$(tid('lock'))), 5000, 'unlocked'); check('…and opens with the right PIN', true)
  await page.click(tid('set-lock'))
  check('no console errors / uncaught exceptions during the whole run', errors.length === 0, errors.slice(0, 3).join(' | '))
} catch (e) {
  console.log('FAIL  unexpected: ' + e.message); failed++
  await page.screenshot({ path: `${SHOTS}/terminal-failure.png` }).catch(() => {})
} finally {
  await ctx.request.put(`${URL_}/api/admin/terminal`, { data: { delay_s: original.delay_s, tz: original.tz, tf: original.tf, volume: original.volume } }).catch(() => {})
  await ctx.request.post(`${URL_}/api/trade/account/reset`, { data: { confirm: true } }).catch(() => {})
  await browser.close()
}
console.log(failed ? `\n${failed} CHECK(S) FAILED` : '\nALL TERMINAL E2E CHECKS PASSED')
process.exit(failed ? 1 : 0)
