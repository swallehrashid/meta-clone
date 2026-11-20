import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { getQuotes, getTerminal, type TerminalSettings } from '../lib/api'
import { installDebug, registerDebug } from '../lib/debug'
import { MarketStore } from '../lib/store'
import { LabStream } from '../lib/stream'
import {
  cancelOrder, closePosition, getHistory, getOrderHistory, getTradeState, modifyOrder, modifyPosition, placeOrder, TradeError,
  type Deal, type TOrder, type TradeState,
} from '../lib/trade'
import type { Timeframe } from '../lib/types'
import { ChartScreen } from './Chart'
import { Ctx, type NewOrder, type PageSpec, type PanelMode, type QuoteView, type T, type Tab } from './ctx'
import { money, vol } from './format'
import { HistoryScreen } from './History'
import { NCharts, NHistory, NMessages, NQuotes, NTrade } from './icons'
import { log } from './journal'
import { OrderPanel, defaultPrices, newDraft, type Draft, type OType } from './OrderPanel'
import {
  AccountsPage, CalendarScreen, ChartSettingsPage, Drawer, IndicatorListPage, IndicatorParamsPage, IndicatorsPage, JournalScreen, LinkScreen, LockScreen,
  MailboxScreen, MailPage, MessagesScreen, NewsScreen, OtpPage, SettingsScreen,
} from './Pages'
import { usePrefs, type TfName } from './prefs'
import { AddSymbolPage, FolderPage, PropertiesPage, QuotesScreen, SelectedSymbolsPage, StatisticsPage } from './Quotes'
import { ReportPage } from './Report'
import { sounds, vibrate } from './sound'
import { TradeScreen } from './Trade'
import './terminal.css'

installDebug()
const SERVER_TF = (tf: TfName): Timeframe => (tf === 'W1' || tf === 'MN' ? 'D1' : tf)
// History entries we push are tagged with this load's id: after a reload, entries left over from the previous load are never
// rewound through (that could walk the browser right out of the app).
const LOAD_ID = Math.random().toString(36).slice(2)
const nav = { depth: 0, skip: 0 }
function mark() { try { history.pushState({ mt5: LOAD_ID }, ''); nav.depth++ } catch { /* ignore */ } }
function rewind(n = 1) {
  const k = Math.min(n, nav.depth)
  if (k > 0 && history.state?.mt5 === LOAD_ID) { nav.skip++; nav.depth -= k; history.go(-k) }
}

/** The terminal: a replica of MetaTrader 5 for Android. The server decides delay, pairs and account; this app is the screen. */
export default function Terminal() {
  const [cfg, setCfg] = useState<TerminalSettings | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    let dead = false
    const load = () => getTerminal()
      .then((c) => { if (!dead) { setErr(''); setCfg((prev) => (JSON.stringify(prev) === JSON.stringify(c) ? prev : c)) } })
      .catch((e) => { if (!dead) setErr(String(e.message ?? e)) })
    load()
    const id = setInterval(load, 4000)
    return () => { dead = true; clearInterval(id) }
  }, [])
  if (!cfg) return <div className="boot">{err ? `Cannot connect: ${err}` : 'Connecting…'}</div>
  return <Shell cfg={cfg} />
}

function Shell({ cfg }: { cfg: TerminalSettings }) {
  const [prefs, setPrefs] = usePrefs()
  const allSymbols = useMemo(() => cfg.symbols.map((s) => cfg.broker_symbols[s] ?? s), [cfg.symbols, cfg.broker_symbols])
  const symbols = useMemo(() => {
    const vis = allSymbols.filter((s) => !prefs.hidden.includes(s))
    return [...prefs.order.filter((s) => vis.includes(s)), ...vis.filter((s) => !prefs.order.includes(s))]
  }, [allSymbols, prefs.hidden, prefs.order])
  const baseOf = useCallback((b: string) => cfg.symbols.find((s) => (cfg.broker_symbols[s] ?? s) === b) ?? b, [cfg])
  const describe = useCallback((b: string) => cfg.descriptions[baseOf(b)] ?? b, [cfg, baseOf])

  // ------------------------------------------------------------ navigation (Android back pops pages / closes overlays)
  const [tab, setTabState] = useState<Tab>(() => { try { return (sessionStorage.getItem('mt5.tab') as Tab) || 'quotes' } catch { return 'quotes' } })
  const [pages, setPages] = useState<PageSpec[]>([])
  const [drawer, setDrawer] = useState(false)
  const setTab = useCallback((x: Tab) => { setTabState(x); setPages([]); try { sessionStorage.setItem('mt5.tab', x) } catch { /* private mode */ } }, [])
  const push = useCallback((pg: PageSpec) => { setPages((s) => [...s, pg]); mark() }, [])
  const pop = useCallback(() => setPages((s) => s.slice(0, -1)), [])
  const overlays = useRef({ pages: 0, drawer: false, panel: false })

  // ------------------------------------------------------------ chart symbol / timeframe
  const chartSym = prefs.symbol && allSymbols.includes(prefs.symbol) ? prefs.symbol : symbols[0] ?? allSymbols[0]
  const tf: TfName = prefs.tf ?? (cfg.tf as TfName)
  const setChartSym = useCallback((s: string) => setPrefs({ symbol: s }), [setPrefs])
  const setTf = useCallback((x: TfName) => setPrefs({ tf: x }), [setPrefs])

  // ------------------------------------------------------------ delayed chart feed (one socket)
  const store = useMemo(() => new MarketStore(), [])
  useSyncExternalStore(store.subscribe, store.getVersion)
  const stream = useRef<LabStream | null>(null)
  useEffect(() => {
    stream.current = new LabStream('stream', { symbols: [chartSym], tf: SERVER_TF(tf), delay: cfg.delay_s }, store)
    log('Network', `'${cfg.login}': authorized on ${cfg.server_name}`)
    return () => { stream.current?.close(); stream.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { stream.current?.setSymbols([chartSym]); store.prune([chartSym]) }, [chartSym, store])
  useEffect(() => { stream.current?.setTf(SERVER_TF(tf)) }, [tf])
  useEffect(() => { stream.current?.setDelay(cfg.delay_s) }, [cfg.delay_s])

  // ------------------------------------------------------------ quotes for every pair (Quotes window, margin, cross rates)
  const [quotes, setQuotes] = useState<Map<string, QuoteView>>(new Map())
  useEffect(() => {
    let dead = false
    const run = () => getQuotes().then((r) => {
      if (dead) return
      setQuotes((old) => {
        const m = new Map<string, QuoteView>()
        for (const q of r.quotes) {
          const o = old.get(q.symbol)
          m.set(q.symbol, { ...q, stale: r.cutoff_ms - q.t_ms > 15 * 60_000,
            bidUp: o ? (q.bid > o.bid ? true : q.bid < o.bid ? false : o.bidUp) : q.change_pts >= 0,
            askUp: o ? (q.ask > o.ask ? true : q.ask < o.ask ? false : o.askUp) : q.change_pts >= 0 })
        }
        return m
      })
    }).catch(() => { /* next poll */ })
    run()
    const id = setInterval(run, tab === 'quotes' ? 1000 : 2000)
    return () => { dead = true; clearInterval(id) }
  }, [tab])

  // ------------------------------------------------------------ the account
  const cfgRef = useRef(cfg)
  cfgRef.current = cfg
  const [trade, setTrade] = useState<TradeState | null>(null)
  const refreshTrade = useCallback(async () => { try { setTrade(await getTradeState(cfgRef.current.delay_s)) } catch { /* clock catching up */ } }, [])
  useEffect(() => { refreshTrade(); const id = setInterval(refreshTrade, 1000); return () => clearInterval(id) }, [refreshTrade])
  const [deals, setDeals] = useState<Deal[]>([])
  const [ordersHistory, setOrdersHistory] = useState<TOrder[]>([])
  const refreshHistory = useCallback(async () => {
    try { const [h, o] = await Promise.all([getHistory(5000), getOrderHistory(5000)]); setDeals(h.deals); setOrdersHistory(o.orders) } catch { /* retry */ }
  }, [])
  const needHistory = tab === 'history' || pages.some((p) => p.id === 'report')
  useEffect(() => { if (!needHistory) return; refreshHistory(); const id = setInterval(refreshHistory, 3000); return () => clearInterval(id) }, [needHistory, refreshHistory])
  // a position that closed by SL / TP / stop out: say so, like MT5's notification + journal
  const seenDeals = useRef<number | null>(null)
  useEffect(() => {
    const n = trade?.account.deals ?? null
    if (n === null) return
    if (seenDeals.current !== null && n > seenDeals.current) {
      getHistory(n - seenDeals.current).then((r) => { for (const d of r.deals) if (d.reason !== 'manual') {
        log('Trades', `'${cfg.login}': ${d.reason === 'sl' ? 'stop loss' : d.reason === 'tp' ? 'take profit' : d.reason} triggered #${d.position_id} ${d.side} ${vol(d.volume)} ${d.symbol} at ${d.close_price}`)
        toast(`${d.reason === 'sl' ? 'Stop loss' : d.reason === 'tp' ? 'Take profit' : 'Position closed'}: #${d.position_id} ${d.symbol} ${money(d.net)}`)
      } }).catch(() => undefined)
      if (needHistory) refreshHistory()
    }
    seenDeals.current = n
  }, [trade?.account.deals])                                           // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------ feedback
  const [toastMsg, setToast] = useState<{ text: string; bad: boolean } | null>(null)
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const toast = useCallback((text: string, bad = false) => {
    setToast({ text, bad })
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToast(null), bad ? 4500 : 2600)
  }, [])
  const feedback = (ok: boolean) => { if (prefs.orderSounds) (ok ? sounds.ok : sounds.error)(); vibrate(prefs.vibration, ok ? 25 : 80) }
  const fail = (e: unknown, what: string) => {
    const te = e as TradeError
    const msg = te.code === 'clock_behind' ? 'Trade is disabled: the chart is catching up with the account clock' : te.message
    log('Trades', `'${cfg.login}': failed ${what} [${msg}]`)
    toast(msg, true); feedback(false)
    return false
  }

  // ------------------------------------------------------------ trading actions
  const place = useCallback(async (o: NewOrder) => {
    const what = `${o.type === 'market' ? 'market' : o.type.replace('_', ' ')} ${o.side} ${vol(o.volume)} ${o.symbol}${o.price ? ` at ${o.price}` : ''}`
    try {
      const r = await placeOrder({ symbol: o.symbol, side: o.side, type: o.type, volume: o.volume, price: o.price ?? null, stoplimit: o.stoplimit ?? null,
        sl: o.sl ?? null, tp: o.tp ?? null, delay: cfgRef.current.delay_s, peeked: false, session_id: 'terminal', comment: o.comment ?? '',
        expiration: o.expiration ?? 'gtc', expire_ms: o.expire_ms ?? null })
      setTrade({ account: r.account, positions: r.positions, orders: r.orders })
      const d = r.price !== null ? r.price.toFixed(store.data.get(o.symbol)?.digits ?? quotes.get(o.symbol)?.digits ?? 5) : ''
      log('Trades', `'${cfg.login}': ${what}${o.sl ? ` sl: ${o.sl}` : ''}${o.tp ? ` tp: ${o.tp}` : ''} ${r.status === 'filled' ? `done at ${d}` : 'placed'}`)
      toast(r.status === 'filled' ? `${o.side} ${vol(o.volume)} ${o.symbol} at ${d} — done` : `${o.side} ${o.type.replace('_', ' ')} ${vol(o.volume)} ${o.symbol} at ${o.price} — placed`)
      feedback(true)
      return true
    } catch (e) { return fail(e, what) }
  }, [cfg.login, prefs.orderSounds, prefs.vibration, quotes, store])  // eslint-disable-line react-hooks/exhaustive-deps
  const closePos = useCallback(async (id: number, volume?: number) => {
    const pos = trade?.positions.find((x) => x.id === id)
    const what = `close #${id}${pos ? ` ${pos.side} ${vol(volume ?? pos.volume)} ${pos.symbol}` : ''}`
    try {
      setTrade(await closePosition(id, cfgRef.current.delay_s, volume))
      log('Trades', `'${cfg.login}': ${what} done`)
      toast(`Position #${id} closed${pos?.profit !== undefined && pos.profit !== null ? ` — ${money(pos.profit)}` : ''}`); feedback(true)
      if (needHistory) refreshHistory()
      return true
    } catch (e) { return fail(e, what) }
  }, [trade, cfg.login, needHistory]) // eslint-disable-line react-hooks/exhaustive-deps
  const modifyPos = useCallback(async (id: number, sl: number | null, tp: number | null) => {
    try { setTrade(await modifyPosition(id, cfgRef.current.delay_s, { sl, tp })); log('Trades', `'${cfg.login}': modify #${id} sl: ${sl ?? 0} tp: ${tp ?? 0} done`); toast(`Position #${id} modified`); feedback(true); return true }
    catch (e) { return fail(e, `modify #${id}`) }
  }, [cfg.login]) // eslint-disable-line react-hooks/exhaustive-deps
  const modifyOrd = useCallback(async (id: number, change: { price?: number; sl?: number | null; tp?: number | null; stoplimit?: number | null }) => {
    try { setTrade(await modifyOrder(id, cfgRef.current.delay_s, change)); log('Trades', `'${cfg.login}': modify order #${id} done`); toast(`Order #${id} modified`); feedback(true); return true }
    catch (e) { return fail(e, `modify order #${id}`) }
  }, [cfg.login]) // eslint-disable-line react-hooks/exhaustive-deps
  const cancelOrd = useCallback(async (id: number) => {
    try { setTrade(await cancelOrder(id, cfgRef.current.delay_s)); log('Trades', `'${cfg.login}': cancel order #${id} done`); toast(`Order #${id} deleted`); feedback(true); return true }
    catch (e) { return fail(e, `cancel order #${id}`) }
  }, [cfg.login]) // eslint-disable-line react-hooks/exhaustive-deps

  // ------------------------------------------------------------ the order panel and its draft
  const [panel, setPanelState] = useState<PanelMode | null>(null)
  const panelRef = useRef<PanelMode | null>(null)
  panelRef.current = panel
  const volume = prefs.volume ?? cfg.volume
  const [draft, setDraft] = useState<Draft>(() => newDraft(chartSym, volume))
  const setPanel = useCallback((m: PanelMode | null) => {
    if (m) {
      if (m.kind === 'new') {
        const q = quotes.get(m.symbol), tk = store.data.get(m.symbol)?.lastTick
        const otype = (m.type as OType | undefined) ?? 'market'
        const d = newDraft(m.symbol, volume, otype)
        const bid = tk?.bid ?? q?.bid, ask = tk?.ask ?? q?.ask
        if (bid !== undefined && ask !== undefined) Object.assign(d, defaultPrices(otype, bid, ask, q?.digits ?? store.data.get(m.symbol)?.digits ?? 5))
        setDraft(d)
      } else if (m.kind === 'close' || m.kind === 'modify') {
        const p = trade?.positions.find((x) => x.id === m.positionId)
        if (p) setDraft({ ...newDraft(p.symbol, p.volume), volume: p.volume, sl: p.sl, tp: p.tp, stops: true })
      } else {
        const o = trade?.orders.find((x) => x.id === m.orderId)
        if (o) setDraft({ ...newDraft(o.symbol, o.volume), price: o.price, stoplimit: o.stoplimit ?? null, sl: o.sl, tp: o.tp, stops: true })
      }
      mark()
    } else if (panelRef.current) rewind()                                // closed with its own controls: drop its history entry
    setPanelState(m)
  }, [quotes, store, volume, trade])

  // Android / browser back: close the top-most overlay instead of leaving the app. Our own back buttons change the state
  // directly and rewind history quietly (skipPop), so one tap never closes two things.
  overlays.current = { pages: pages.length, drawer, panel: !!panel }
  useEffect(() => {
    const onPop = () => {
      if (nav.skip) { nav.skip--; return }
      nav.depth = Math.max(0, nav.depth - 1)
      const o = overlays.current
      if (o.drawer) setDrawer(false)
      else if (o.panel) setPanelState(null)
      else if (o.pages) setPages((s) => s.slice(0, -1))
    }
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  const back = useCallback(() => { pop(); rewind() }, [pop])
  const popN = useCallback((n: number) => { setPages((st) => st.slice(0, -n)); rewind(n) }, [])

  // ------------------------------------------------------------ lock screen, always-on display
  const [locked, setLocked] = useState(() => prefs.lockScreen && !!prefs.pin)
  useEffect(() => {
    const f = () => { if (document.hidden && prefs.lockScreen && prefs.pin) setLocked(true) }
    document.addEventListener('visibilitychange', f)
    return () => document.removeEventListener('visibilitychange', f)
  }, [prefs.lockScreen, prefs.pin])
  useEffect(() => {
    if (!prefs.alwaysOn) return
    let lock: { release: () => Promise<void> } | null = null
    const nav = navigator as unknown as { wakeLock?: { request: (k: string) => Promise<{ release: () => Promise<void> }> } }
    nav.wakeLock?.request('screen').then((l) => { lock = l }).catch(() => undefined)
    return () => { void lock?.release() }
  }, [prefs.alwaysOn])

  const t: T = {
    cfg, prefs, setPrefs, store, allSymbols, symbols, describe,
    digitsOf: (s) => store.data.get(s)?.digits ?? quotes.get(s)?.digits ?? (s.includes('JPY') ? 3 : 5),
    quotes, trade, deals, ordersHistory, refreshTrade, refreshHistory,
    tab, setTab, push, pop: back, openDrawer: () => { setDrawer(true); mark() },
    chartSym, setChartSym, tf, setTf, panel, setPanel, toast, place, closePos, modifyPos, modifyOrd, cancelOrd,
  }

  useEffect(() => registerDebug('terminal', () => ({
    tab, pages: pages.map((p) => p.id), symbol: chartSym, tf, delay: cfg.delay_s, status: store.status, cutoffMs: store.cutoffMs,
    bars: store.data.get(chartSym)?.bars.length ?? 0, equity: trade?.account.equity ?? null, balance: trade?.account.balance ?? null,
    positions: trade?.positions.length ?? 0, orders: trade?.orders.length ?? 0, quotes: quotes.size, theme: prefs.theme, panel: panel?.kind ?? null,
    draft: { otype: draft.otype, price: draft.price, stoplimit: draft.stoplimit, sl: draft.sl, tp: draft.tp, volume: draft.volume, symbol: draft.symbol },
    font: getComputedStyle(document.querySelector('.mt5') ?? document.body).fontFamily, scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
  })))

  const a = trade?.account
  const floating = a && trade!.positions.length ? a.unrealized : null
  const pill = !prefs.accountInMenu ? null : floating !== null ? money(floating).replace(/ /g, ' ') : a ? a.balance.toFixed(2) : '–'
  const top = pages[pages.length - 1]
  const page = top && (() => {
    switch (top.id) {
      case 'addSymbol': return <AddSymbolPage onBack={back} />
      case 'folder': return <FolderPage folder={top.folder} onBack={back} />
      case 'selectedSymbols': return <SelectedSymbolsPage onBack={back} />
      case 'properties': return <PropertiesPage symbol={top.symbol} onBack={back} />
      case 'statistics': return <StatisticsPage symbol={top.symbol} onBack={back} />
      case 'indicators': return <IndicatorsPage onBack={back} />
      case 'indicatorList': return <IndicatorListPage window={top.window} onBack={back} />
      case 'indicatorParams': return <IndicatorParamsPage type={top.type} window={top.window} editId={top.editId} onBack={back}
        onDone={() => popN(top.editId ? 1 : 2)} />
      case 'chartSettings': return <ChartSettingsPage onBack={back} />
      case 'report': return <ReportPage onBack={back} />
      case 'mail': return <MailPage idx={top.idx} onBack={back} />
      case 'accounts': return <AccountsPage onBack={back} />
      case 'otp': return <OtpPage onBack={back} />
    }
  })()
  const navBtn = (x: Tab, label: string, icon: React.ReactNode, extra?: React.ReactNode) => (
    <button className={tab === x ? 'on' : ''} onClick={() => setTab(x)} data-testid={`nav-${x}`}>{icon}{extra ?? <span>{label}</span>}</button>
  )

  return (
    <Ctx.Provider value={t}>
      <div className={`mt5${prefs.tablet ? ' tablet' : ''}`} data-theme={prefs.theme} data-testid="terminal">
        <div className="main">
          {tab === 'quotes' && <QuotesScreen />}
          {tab === 'chart' && <ChartScreen draft={draft} setDraft={setDraft} />}
          {tab === 'trade' && <TradeScreen />}
          {tab === 'history' && <HistoryScreen />}
          {tab === 'messages' && <MessagesScreen />}
          {tab === 'settings' && <SettingsScreen />}
          {tab === 'news' && <NewsScreen />}
          {tab === 'mailbox' && <MailboxScreen />}
          {tab === 'journal' && <JournalScreen />}
          {tab === 'calendar' && <CalendarScreen />}
          {(tab === 'community' || tab === 'algo' || tab === 'guide' || tab === 'about') && <LinkScreen kind={tab} />}
          {page}
          {panel && tab !== 'chart' && <OrderPanel key={`${panel.kind}-full`} mode={panel} draft={draft} setDraft={setDraft} full onClose={() => setPanel(null)} />}
        </div>
        {!top && !(panel && tab === 'chart') && (
          <nav className="nav">
            {navBtn('quotes', 'Quotes', <NQuotes />)}
            {navBtn('chart', 'Charts', <NCharts />)}
            {navBtn('trade', 'Trade', <NTrade />, pill !== null ? <span className={`pill${floating !== null ? (floating < 0 ? ' neg' : ' pos') : ''}`} data-testid="equity-pill">{pill}</span> : undefined)}
            {navBtn('history', 'History', <NHistory />)}
            {navBtn('messages', 'Messages', <NMessages />)}
          </nav>
        )}
        {drawer && <Drawer onClose={() => { setDrawer(false); rewind() }} />}
        {toastMsg && <div className={`toast${toastMsg.bad ? ' bad' : ''}`} role="status" data-testid="toast">{toastMsg.text}</div>}
        {locked && prefs.pin && <LockScreen hash={prefs.pin} onUnlock={() => setLocked(false)} />}
      </div>
    </Ctx.Provider>
  )
}
