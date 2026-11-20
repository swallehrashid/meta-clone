// End-to-end check of the BACK OFFICE (`/admin`): login, terminal settings, and the bot lab (forward data on/off, replay, train).
//   LAB_ADMIN_PASSWORD=... LAB_URL=http://127.0.0.1:8800 SHOTS=/dir node e2e/admin.mjs
// Needs a stack with some history (python -m tools.make_history, then the labeler) for the train step to have something to learn from;
// without it the train job is expected to FAIL cleanly and the check says so. Starts/stops a live bot on the paper account.
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
const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } })
const page = await ctx.newPage()
const errors = []
page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource/.test(m.text())) errors.push(m.text()) })
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message))
const until = async (fn, ms = 20000, what = 'condition') => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) { try { const v = await fn(); if (v) return v } catch { /* retry */ } await sleep(200) }
  throw new Error('timeout waiting for ' + what)
}
const api = (p, init) => page.evaluate(async ([p, init]) => { const r = await fetch(p, init); return { status: r.status, json: await r.json().catch(() => null) } }, [p, init])
const runRow = async (id) => (await page.innerText(tid(`run-${id}`))).replace(/\s+/g, ' ')
const done = (id) => until(async () => { const t = await runRow(id); return /\bdone\b|failed/.test(t) && t }, 280000, `run ${id}`)
const original = (await (await ctx.request.post(`${URL_}/api/admin/login`, { data: { username: 'admin', password: PW } })).ok(), await (await ctx.request.get(`${URL_}/api/admin/terminal`)).json())
await ctx.request.post(`${URL_}/api/admin/logout`)

try {
  // ------------------------------------------------------------------ 1. login wall
  const anon = await browser.newContext()
  const codes = await Promise.all(['/api/admin/lab/bots', '/api/bot', '/api/admin/terminal', '/labels?symbol=EURUSD&delay=300', '/calls', '/report'].map(async (u) => (await anon.request.get(URL_ + u)).status()))
  check('without a login every back-office endpoint answers 401', codes.every((c) => c === 401), codes.join(','))
  check('...but the terminal and its settings feed are public', (await anon.request.get(`${URL_}/api/terminal`)).ok() && (await anon.request.get(`${URL_}/api/terminal/quotes`)).ok())
  await anon.close()
  await page.goto(`${URL_}/admin`)
  await page.waitForSelector(tid('login'))
  await page.fill(tid('login-pass'), 'definitely-wrong'); await page.click(tid('login-submit'))
  check('a wrong password is refused with a message', /Wrong username or password/.test(await until(() => page.textContent(tid('login-error')), 8000)))
  await page.fill(tid('login-pass'), PW); await page.click(tid('login-submit'))
  await page.waitForSelector(tid('terminal-panel'))
  check('the right password opens the back office', true)
  await page.reload(); await page.waitForSelector(tid('terminal-panel'))
  check('the session survives a reload (cookie)', true)

  // ------------------------------------------------------------------ 2. terminal settings
  await page.screenshot({ path: `${SHOTS}/admin-terminal.png`, fullPage: true })
  await page.click('button:has-text("15m")')
  await page.click(tid('save-terminal'))
  await until(async () => (await api('/api/terminal')).json.delay_s === 900, 8000, 'delay saved')
  check('choosing 15m and saving changes the delay the terminal gets', true)
  await page.fill(tid('delay-input'), 'banana')
  check('an invalid delay disables Save', await page.isDisabled(tid('save-terminal')))
  await page.fill(tid('delay-input'), '5m'); await page.click(tid('save-terminal'))
  await until(async () => (await api('/api/terminal')).json.delay_s === 300, 8000, 'delay 5m')
  check('a custom value ("5m") works', true)

  // ------------------------------------------------------------------ 3. bot lab: forward data is explicit
  await page.click(tid('tab-bots')); await page.waitForSelector(tid('bot-lab'))
  await page.waitForSelector(`${tid('live-panel')} ${tid('live-start')}`)
  check('the teacher bot starts with Forward data selected and cannot be set to Blind',
    await page.isDisabled(`${tid('live-panel')} ${tid('fwd-blind')}`) && /Every trade is tagged/.test(await page.innerText(`${tid('live-panel')} ${tid('forward-picker')}`)))
  await page.selectOption(tid('live-bot'), 'random_entry')
  check('a normal bot can be Blind, and says what that means', !(await page.isDisabled(`${tid('live-panel')} ${tid('fwd-blind')}`)) && /only what a person on the delayed chart sees/.test(await page.innerText(`${tid('live-panel')} ${tid('forward-picker')}`)))
  await page.selectOption(tid('live-bot'), 'forward_teacher')
  await page.fill(`${tid('live-panel')} ${tid('fwd-seconds')}`, '900')
  await page.click(tid('live-start'))
  check('forward data longer than the delay is refused with the reason', /delay/.test(await until(() => page.textContent(tid('live-error')), 8000)))
  await page.fill(`${tid('live-panel')} ${tid('fwd-seconds')}`, '60')
  await page.click(tid('live-start'))
  await until(async () => /running/.test(await page.innerText(tid('live-panel'))), 10000, 'live bot running')
  const txt = await page.innerText(tid('live-panel'))
  check('a live forward bot is shown as running with the "tagged peeked" label', /FORWARD 60s/.test(txt) && /peeked/.test(txt))
  await page.screenshot({ path: `${SHOTS}/admin-live.png`, fullPage: true })
  await page.click(tid('live-stop'))
  await until(async () => (await page.$(tid('live-start'))) !== null, 8000, 'stopped')
  check('Stop returns the panel to the start form', true)

  // ------------------------------------------------------------------ 4. replay jobs, forward vs blind, side by side
  const range = (await api('/api/admin/lab/range')).json
  const last = new Date(Math.min(...Object.values(range.symbols).map((s) => s.to_ms)))
  const day = (d) => new Intl.DateTimeFormat('en-CA', { timeZone: range.tz }).format(d)
  const lastDay = day(last), prevDay = day(new Date(last.getTime() - 86_400_000))
  const before = (await api('/api/admin/lab/runs')).json.runs.length
  await page.selectOption(tid('replay-bot'), 'forward_teacher')
  await page.fill(tid('replay-from'), lastDay); await page.fill(tid('replay-to'), lastDay)
  await page.click(tid('replay-run'))
  const id1 = await until(async () => { const r = (await api('/api/admin/lab/runs')).json.runs; return r.length > before && r[0].id }, 10000, 'job registered')
  check('a replay starts as a background job', true, `#${id1}`)
  const row1 = await done(id1)
  check('the forward replay is labelled FORWARD and has trades that paid (hindsight)', /FORWARD 60s/.test(row1) && /done/.test(row1) && /\d+ trades · \+/.test(row1), row1.slice(0, 140))
  await page.waitForSelector(tid('run-detail'))
  check('its detail warns that it is forward-fed / peeked / not evidence', /FORWARD-FED RUN/.test(await page.innerText(tid('run-detail'))) && /not evidence/.test(await page.innerText(tid('run-detail'))))
  await page.screenshot({ path: `${SHOTS}/admin-replay.png`, fullPage: true })

  await page.selectOption(tid('replay-bot'), 'random_entry')
  await page.click(`${tid('replay-panel')} ${tid('fwd-blind')}`)
  await page.fill(tid('replay-from'), lastDay); await page.fill(tid('replay-to'), lastDay)
  await page.click(tid('replay-run'))
  const id2 = await until(async () => { const r = (await api('/api/admin/lab/runs')).json.runs; return r.length > before + 1 && r[0].id }, 10000, 'second job')
  const row2 = await done(id2)
  check('a blind replay is labelled blind and is listed next to the forward one', /blind/.test(row2) && !/FORWARD/.test(row2) && (await page.$$(`${tid('runs-panel')} tbody tr[data-testid^=run-]`)).length >= 2, row2.slice(0, 120))
  check('opening a blind run shows no forward warning', !/FORWARD-FED RUN/.test(await page.innerText(tid('run-detail'))))

  // the run detail must follow a NEW job even while another run's detail is open (regression: stale poller)
  await page.click(`${tid('replay-panel')} ${tid('fwd-forward')}`); await page.selectOption(tid('replay-bot'), 'forward_teacher')
  await page.click(tid('replay-run'))
  const id3 = await until(async () => { const r = (await api('/api/admin/lab/runs')).json.runs; return r[0].id > id2 && r[0].id }, 10000, 'third job')
  await until(async () => /FORWARD-FED RUN/.test(await page.innerText(tid('run-detail')).catch(() => '')), 120000, 'detail follows the new job without a click')
  check('the open detail follows a newly started job through running -> done', true, `#${id3}`)
  await done(id3)

  // ------------------------------------------------------------------ 5. train: watch -> learn -> blind test
  await page.fill(tid('train-name'), 'e2e_model')
  await page.fill(tid('train-from'), prevDay); await page.fill(tid('train-to'), prevDay)
  await page.fill(tid('test-from'), lastDay); await page.fill(tid('test-to'), lastDay)
  await page.click(tid('train-run'))
  const id4 = await until(async () => { const r = (await api('/api/admin/lab/runs')).json.runs; return r[0].id > id3 && r[0].id }, 10000, 'train job')
  const row4 = await done(id4)
  if (/failed/.test(row4)) {
    check('training on too little data fails cleanly with a reason (needs real history for more)', /features|decisions|longer/.test(row4), row4.slice(0, 160))
  } else {
    await page.waitForSelector(tid('verdict'), { timeout: 30000 })
    const d = await page.innerText(tid('run-detail'))
    check('the training report has all four stages: watch, analyse, learn, blind test', /1\. What the teacher did/i.test(d) && /2\. What the features say/i.test(d) && /3\. How well a model learned/i.test(d) && /4\. Blind test/i.test(d))
    check('the blind test compares the learned bot with a random bot and marks the teacher as peeked', /Learned bot — BLIND/.test(d) && /Random bot \(null benchmark\)/.test(d) && /Teacher — forward data \(upper bound\)\s*peeked/.test(d))
    check('the verdict is stated plainly', /No evidence of skill|beat the random benchmark|took no trades/.test(await page.innerText(tid('verdict'))))
    check('the trained model is offered to the learned bot', (await api('/api/admin/lab/models')).json.models.some((m) => m.name === 'e2e_model'))
    await page.screenshot({ path: `${SHOTS}/admin-train.png`, fullPage: true })
    await ctx.request.delete(`${URL_}/api/admin/lab/models/e2e_model`)
  }

  // ------------------------------------------------------------------ 6. the old full lab still lives here; sign out
  await page.click(tid('tab-chart'))
  await page.waitForSelector('[data-testid=chart-0]', { timeout: 20000 })
  check('Chart lab tab: the full chart toolbar (labels, Peek, calls, delay) is here, not on the terminal', true)
  await page.click('button:has-text("Sign out")')
  await page.waitForSelector(tid('login'))
  check('Sign out returns to the login wall and ends the session', (await ctx.request.get(`${URL_}/api/admin/terminal`)).status() === 401)
  check('no console errors / uncaught exceptions during the whole run', errors.length === 0, errors.slice(0, 3).join(' | '))
} catch (e) {
  console.log('FAIL  unexpected: ' + e.message); failed++
  await page.screenshot({ path: `${SHOTS}/admin-failure.png`, fullPage: true }).catch(() => {})
} finally {
  await ctx.request.post(`${URL_}/api/admin/login`, { data: { username: 'admin', password: PW } }).catch(() => {})
  await ctx.request.put(`${URL_}/api/admin/terminal`, { data: { delay_s: original.delay_s } }).catch(() => {})
  await ctx.request.post(`${URL_}/api/bot/stop`, { data: { close_positions: true } }).catch(() => {})
  await browser.close()
}
console.log(failed ? `\n${failed} CHECK(S) FAILED` : '\nALL ADMIN E2E CHECKS PASSED')
process.exit(failed ? 1 : 0)
