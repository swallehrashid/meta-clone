import { chromium } from 'playwright-core'
// node shotpage.mjs <url> <outPrefix> <width> <y0:h> [<y0:h> ...]
const [url, prefix, w, ...clips] = process.argv.slice(2)
const b = await chromium.launch({ channel: 'chrome', headless: true })
const ctx = await b.newContext({ viewport: { width: +w, height: 900 }, deviceScaleFactor: 1.5 })
const page = await ctx.newPage()
await page.goto(url)
const H = await page.evaluate(() => document.documentElement.scrollHeight)
const W = await page.evaluate(() => document.documentElement.scrollWidth)
console.log(`page ${W}x${H}`)
let i = 0
for (const c of clips) { const [y, h] = c.split(':').map(Number); await page.screenshot({ path: `${prefix}_${i++}.png`, fullPage: true, clip: { x: 0, y, width: +w, height: Math.min(h, H - y) } }) }
await b.close()
