import { chromium } from 'playwright-core'
const [url, out] = process.argv.slice(2)
const b = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await b.newContext({ viewport: { width: 844, height: 390 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true })
const page = await ctx.newPage()
await page.goto(url)
await page.waitForFunction(() => window.__lab?.state().app?.status === 'open' && window.__lab.state()['pane:chart-0']?.bars > 10, null, { timeout: 30000 })
await page.tap('[data-testid=layout-2]'); await page.tap('[data-testid=toggle-peek]'); await page.waitForTimeout(2500)
const r = await page.evaluate(() => { const q = (s) => document.querySelector(s)?.getBoundingClientRect(); const a = window.__lab.state().app
  return { overflow: a.scrollW > a.innerW, toolbarH: q('.toolbar').height, chartsH: q('.charts').height, peekW: q('.peek-pane')?.width, tradebarH: q('.tradebar').height, buyH: q('[data-testid=buy]').height } })
console.log(JSON.stringify(r)); await page.screenshot({ path: out }); await b.close()
