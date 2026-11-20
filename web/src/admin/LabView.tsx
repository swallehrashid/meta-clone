import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { CallsPanel } from '../components/CallsPanel'
import { ChartPane } from '../components/ChartPane'
import { Toolbar } from '../components/Toolbar'
import { TradeBar } from '../components/TradeBar'
import { TradeSheet, type SheetTab } from '../components/TradeSheet'
import { getAdminTerminal, getCalls, getConfig, getLabels, getScorecard, postCall, putAdminTerminal } from '../lib/api'
import { installDebug, registerDebug } from '../lib/debug'
import type { Level } from '../lib/levels'
import { levelFromPips, fmtMoney } from '../lib/tradeMath'
import { getBot, startBot, stopBot, type BotStatus } from '../lib/bot'
import { cancelOrder, closeAll, closePosition, getEquity, getHistory, getTradeState, modifyOrder, modifyPosition, placeOrder,
         resetAccount, TradeError, type Deal, type EquityPoint, type OrderType, type TradeState } from '../lib/trade'
import { LabelsCache } from '../lib/labels'
import { loadSettings, newSessionId, saveSettings, visibleSymbols, type Settings } from '../lib/settings'
import { MarketStore } from '../lib/store'
import { LabStream } from '../lib/stream'
import { formatDelay, hhmmss, TIMEZONES } from '../lib/time'
import type { Call, Direction, Label, PublicConfig, Scorecard } from '../lib/types'

installDebug()

/** The back office's chart lab: the full toolbar (delay, bid/ask, labels, peek, practice calls, paper trading, bot tab).
 *  The delay here IS the terminal's delay (a server setting): changing it changes what the phone-style terminal shows. */
export default function LabView() {
  const [cfg, setCfg] = useState<PublicConfig | null>(null)
  const [delay, setDelay] = useState<number | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    Promise.all([getConfig(), getAdminTerminal()]).then(([c, t]) => { setCfg(c); setDelay(t.delay_s) }).catch((e) => setErr(String(e)))
  }, [])
  if (err) return <div className="boot err">Cannot reach the server: {err}</div>
  if (!cfg || delay === null) return <div className="boot dim">Loading…</div>
  return <Lab cfg={cfg} terminalDelay={delay} />
}

function Lab({ cfg, terminalDelay }: { cfg: PublicConfig; terminalDelay: number }) {
  const [s, setS] = useState<Settings>(() => ({ ...loadSettings(cfg), delay: terminalDelay }))
  const set = useCallback((patch: Partial<Settings>) => setS((o) => {
    const n = { ...o, ...patch }
    saveSettings(n)
    if (patch.delay !== undefined && patch.delay !== o.delay) putAdminTerminal({ delay_s: patch.delay }).catch(() => { /* shown on the next poll */ })
    return n
  }), [])
  const delayed = useMemo(() => new MarketStore(), [])
  const peekStore = useMemo(() => new MarketStore(), [])
  const sid = useMemo(() => newSessionId(), [])
  useSyncExternalStore(delayed.subscribe, delayed.getVersion)

  const symbols = visibleSymbols(s)
  const symKey = symbols.join(',')
  const activeSym = s.symbols[s.active]

  // ------------------------------------------------------------ delayed stream (one socket, one cutoff)
  const stream = useRef<LabStream | null>(null)
  useEffect(() => {
    stream.current = new LabStream('stream', { symbols, tf: s.tf, delay: s.delay }, delayed)
    return () => { stream.current?.close(); stream.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  useEffect(() => { stream.current?.setSymbols(symbols); delayed.prune(symbols) }, [symKey]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { stream.current?.setTf(s.tf) }, [s.tf])
  useEffect(() => { stream.current?.setDelay(s.delay) }, [s.delay])

  // ------------------------------------------------------------ peek (explicit; never restored)
  const [peekOn, setPeekOn] = useState(false)
  const peekStream = useRef<LabStream | null>(null)
  useEffect(() => {
    if (!peekOn) return
    peekStream.current = new LabStream('peek', { symbols: [activeSym], tf: s.tf }, peekStore,
                                       `&peeked=1&session_id=${encodeURIComponent(sid)}`)
    return () => { peekStream.current?.close(); peekStream.current = null; peekStore.clear() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [peekOn])
  useEffect(() => { peekStream.current?.setSymbols([activeSym]) }, [activeSym])
  useEffect(() => { peekStream.current?.setTf(s.tf) }, [s.tf])

  // ------------------------------------------------------------ practice calls + scorecard
  const [calls, setCalls] = useState<Call[]>([])
  const [score, setScore] = useState<Scorecard | null>(null)
  const [panel, setPanel] = useState(false)
  const [msg, setMsg] = useState('')
  const msgTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const refreshCalls = useCallback(async () => {
    try { const [c, sc] = await Promise.all([getCalls(100), getScorecard()]); setCalls(c.calls); setScore(sc) } catch { /* retry on next tick */ }
  }, [])
  useEffect(() => { refreshCalls(); const id = setInterval(refreshCalls, 2500); return () => clearInterval(id) }, [refreshCalls])
  const say = (text: string) => { setMsg(text); if (msgTimer.current) clearTimeout(msgTimer.current); msgTimer.current = setTimeout(() => setMsg(''), 6000) }

  const onCall = async (dir: Direction) => {
    try {
      const c = await postCall({ symbol: activeSym, direction: dir, delay: delayed.delay || s.delay, peeked: peekOn, session_id: sid })
      say(`${dir.toUpperCase()} saved @ ${hhmmss(Math.floor(c.t_ms / 1000), s.tz)}${c.peeked ? ' (peeked)' : ''}`)
      refreshCalls()
    } catch (e) { say(`Call failed: ${(e as Error).message}`) }
  }

  // ------------------------------------------------------------ paper trading account (Phase 6)
  const [trade, setTrade] = useState<TradeState | null>(null)
  const [sheet, setSheet] = useState(false)
  const [tab, setTab] = useState<SheetTab>('positions')
  const [history, setHistory] = useState<Deal[]>([])
  const [equity, setEquity] = useState<EquityPoint[]>([])
  const [orderType, setOrderType] = useState<OrderType>('market')
  const [orderPrice, setOrderPrice] = useState('')
  const delayRef = useRef(delayed.delay || s.delay)
  delayRef.current = delayed.delay || s.delay
  const refreshTrade = useCallback(async () => {
    try { setTrade(await getTradeState(delayRef.current)) } catch { /* the server may be restarting; try again */ }
  }, [])
  useEffect(() => { refreshTrade(); const id = setInterval(refreshTrade, 1000); return () => clearInterval(id) }, [refreshTrade])
  const refreshSheet = useCallback(async () => {
    try { const [h, e] = await Promise.all([getHistory(200), getEquity()]); setHistory(h.deals); setEquity(e.points) } catch { /* retry */ }
  }, [])
  useEffect(() => { if (!sheet) return; refreshSheet(); const id = setInterval(refreshSheet, 3000); return () => clearInterval(id) }, [sheet, refreshSheet])

  const [botStatus, setBotStatus] = useState<BotStatus | null>(null)
  const refreshBot = useCallback(async () => { try { setBotStatus(await getBot()) } catch { /* retry */ } }, [])
  useEffect(() => { refreshBot(); const id = setInterval(refreshBot, 2500); return () => clearInterval(id) }, [refreshBot])

  const metaOf = (sym: string) => { const d = delayed.data.get(sym); return { digits: d?.digits ?? (sym.endsWith('JPY') ? 3 : 5), pip: d?.pip ?? (sym.endsWith('JPY') ? 0.01 : 0.0001) } }
  const tradeDo = async (what: string, fn: () => Promise<TradeState>) => {
    try { setTrade(await fn()); if (sheet) refreshSheet(); return true }
    catch (e) {
      const te = e as TradeError
      say(te.code === 'clock_behind' ? 'Trading paused: the chart is behind the account clock' : `${what} failed: ${te.message}`)
      refreshTrade()
      return false
    }
  }
  const quote = delayed.data.get(activeSym)?.lastTick ?? null
  useEffect(() => {          // pre-fill the pending-order price with the market when the type or symbol changes
    const d = delayed.data.get(activeSym)
    if (orderType !== 'market' && d?.lastTick) setOrderPrice(d.lastTick.bid.toFixed(d.digits))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderType, activeSym])

  const onSubmit = async (dir: Direction) => {
    if (s.mode === 'practice') return onCall(dir)
    const d = delayed.data.get(activeSym)
    if (!d?.lastTick) return
    let ref = dir === 'buy' ? d.lastTick.bid : d.lastTick.ask      // SL / TP are measured from the exit-side price
    let price: number | null = null
    if (orderType !== 'market') {
      price = Number(orderPrice)
      if (!isFinite(price) || price <= 0) { say('Enter an order price'); return }
      ref = price
    }
    const sl = s.slPips ? levelFromPips(dir, 'sl', ref, s.slPips, d.pip, d.digits) : null
    const tp = s.tpPips ? levelFromPips(dir, 'tp', ref, s.tpPips, d.pip, d.digits) : null
    const label = `${dir.toUpperCase()} ${s.lots.toFixed(2)} ${activeSym}`
    try {
      const r = await placeOrder({ symbol: activeSym, side: dir, type: orderType, volume: s.lots, price, sl, tp, delay: delayRef.current, peeked: peekOn, session_id: sid })
      setTrade({ account: r.account, positions: r.positions, orders: r.orders })
      say(r.status === 'filled' ? `${label} filled @ ${r.price?.toFixed(d.digits)}${peekOn ? ' (peeked)' : ''}` : `${label} ${orderType} order placed`)
      if (sheet) refreshSheet()
    } catch (e) {
      const te = e as TradeError
      say(te.code === 'clock_behind' ? 'Trading paused: the chart is behind the account clock' : `Order refused: ${te.message}`)
    }
  }
  const moveLevel = (lvl: Level, price: number) => {
    const d = delayRef.current
    const name = lvl.field === 'price' ? 'Order' : lvl.field.toUpperCase()
    tradeDo(`Move ${name}`, () => lvl.kind === 'position' ? modifyPosition(lvl.id, d, { [lvl.field]: price }) : modifyOrder(lvl.id, d, { [lvl.field]: price } as { price?: number; sl?: number; tp?: number }))
      .then((ok) => { if (ok) say(`${name} #${lvl.id} moved to ${price}`) })
  }
  const toggleSheet = () => { setSheet((v) => !v); setPanel(false) }

  // ------------------------------------------------------------ labels overlay (off by default)
  const caches = useRef(new Map<string, LabelsCache>())
  const [labelsBy, setLabelsBy] = useState<Record<string, Label[]>>({})
  const labelFetches = useRef(0)
  useEffect(() => {
    if (!s.showLabels) return
    let dead = false
    const run = async () => {
      for (const sym of symbols) {
        let cache = caches.current.get(sym)
        if (!cache) caches.current.set(sym, (cache = new LabelsCache(sym, (q) => { labelFetches.current++; return getLabels(q) })))
        const bars = delayed.data.get(sym)?.bars
        const tFrom = bars?.length ? bars[0].time * 1000 : Date.now() - 3 * 3600_000
        try {
          if ((await cache.refresh(tFrom, s.delay)) && !dead) setLabelsBy((m) => ({ ...m, [sym]: cache!.labels }))
        } catch { /* try again in 5 s */ }
      }
    }
    run()
    const id = setInterval(run, 5000)
    return () => { dead = true; clearInterval(id) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.showLabels, symKey, s.delay, s.tf])

  // ------------------------------------------------------------ debug state for the e2e checks
  useEffect(() => registerDebug('app', () => ({
    status: delayed.status, cutoffMs: delayed.cutoffMs, delay: delayed.delay, tf: s.tf, layout: s.layout,
    active: s.active, symbols, peekOn, peekStatus: peekStore.status, peekCutoffMs: peekStore.cutoffMs,
    calls: calls.length, showLabels: s.showLabels, labelFetches: labelFetches.current, tz: s.tz,
    mode: s.mode, bot: botStatus && { running: botStatus.running, halted: botStatus.halted, name: botStatus.bot?.name ?? null, steps: botStatus.stats?.steps ?? 0, opened: botStatus.stats?.opened ?? 0 }, trade: trade && { balance: trade.account.balance, equity: trade.account.equity, margin: trade.account.margin,
      positions: trade.positions.length, orders: trade.orders.length, tEngine: trade.account.t_engine_ms, deals: trade.account.deals },
    scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
  })))

  const ready = delayed.status === 'open' && (delayed.data.get(activeSym)?.bars.length ?? 0) > 0
  const pendingCalls = calls.filter((c) => c.status === 'pending').length
  const tzLabel = TIMEZONES.find((z) => z.id === s.tz)?.label ?? s.tz
  const dot = delayed.status === 'open' ? 'ok' : delayed.status === 'connecting' ? 'wait' : 'bad'

  const pane = (i: number) => (
    <ChartPane key={i} testId={`chart-${i}`} symbol={s.symbols[i]} store={delayed} tz={s.tz} showBid={s.showBid} showAsk={s.showAsk}
               showLabels={s.showLabels} labelDir={s.labelDir} labelHorizon={s.labelHorizon}
               labels={labelsBy[s.symbols[i]] ?? EMPTY_LABELS} calls={calls} active={i === s.active}
               positions={trade?.positions} orders={trade?.orders} onMoveLevel={moveLevel}
               onActivate={() => i !== s.active && set({ active: i })} />
  )

  return (
    <div className={`app${peekOn ? ' peeking' : ''}`}>
      <Toolbar cfg={cfg} s={s} set={set} peekOn={peekOn} onPeek={() => setPeekOn((v) => !v)}
               setSlotSymbol={(sym) => { const symbols = [...s.symbols]; symbols[s.active] = sym; set({ symbols }) }}
               streamError={delayed.error} />
      <div className="status" data-testid="status">
        <span className={`dot ${dot}`} />
        <span data-testid="chart-time">{delayed.cutoffMs ? `${hhmmss(Math.floor(delayed.cutoffMs / 1000), s.tz)} ${tzLabel}` : '—'}</span>
        <span className="dim"> · chart is {formatDelay(delayed.delay || s.delay)} behind</span>
        {delayed.status !== 'open' && <span className="dim"> · {delayed.status === 'connecting' ? 'connecting…' : 'reconnecting…'}</span>}
      </div>
      {s.mode === 'trade' && trade && (
        <div className="acct-strip" data-testid="acct-strip">
          <span>Bal <b>{fmtMoney(trade.account.balance)}</b></span>
          <span>Eq <b data-testid="strip-equity">{fmtMoney(trade.account.equity)}</b></span>
          <span className={trade.account.unrealized > 0 ? 'pos' : trade.account.unrealized < 0 ? 'neg' : ''}>P&amp;L <b>{fmtMoney(trade.account.unrealized, true)}</b></span>
          {trade.account.margin_level !== null && <span className="dim">Lvl {trade.account.margin_level.toFixed(0)}%</span>}
        </div>
      )}
      {s.mode === 'trade' && trade && trade.account.t_engine_ms > delayed.cutoffMs + 2000 && delayed.cutoffMs > 0 && (
        <div className="peek-banner paused" data-testid="paused-banner">
          Trading paused – the chart is {Math.round((trade.account.t_engine_ms - delayed.cutoffMs) / 1000)} s behind your account clock (the delay was increased)
        </div>
      )}
      {peekOn && <div className="peek-banner" data-testid="peek-banner">PEEK ON – live data visible; calls are marked <b>peeked</b></div>}
      <main className="stage">
        <div className={`charts layout-${s.layout}`} data-testid="charts">
          {Array.from({ length: s.layout }, (_, i) => pane(i))}
        </div>
        {peekOn && (
          <div className="peek-pane" data-testid="peek-pane">
            <ChartPane testId="peek" symbol={activeSym} store={peekStore} tz={s.tz} showBid={s.showBid} showAsk={s.showAsk}
                       showLabels={false} labelDir="buy" labelHorizon="pnl_60s" labels={EMPTY_LABELS} calls={EMPTY_CALLS}
                       active={false} live />
          </div>
        )}
        {panel && <CallsPanel calls={calls} score={score} tz={s.tz} onClose={() => setPanel(false)} />}
        {sheet && (
          <TradeSheet state={trade} history={history} equity={equity} tab={tab} onTab={setTab} tz={s.tz} meta={metaOf} onClose={() => setSheet(false)}
                      onClosePosition={(id, vol) => tradeDo('Close', () => closePosition(id, delayRef.current, vol)).then((ok) => ok && say(vol ? `Closed ${vol} lots of #${id}` : `Closed #${id}`))}
                      onCloseAll={() => tradeDo('Close all', () => closeAll(delayRef.current)).then((ok) => ok && say('All positions closed'))}
                      onModifyPosition={(id, change) => tradeDo('Modify', () => modifyPosition(id, delayRef.current, change))}
                      onModifyOrder={(id, change) => tradeDo('Modify order', () => modifyOrder(id, delayRef.current, change))}
                      onCancelOrder={(id) => tradeDo('Cancel', () => cancelOrder(id, delayRef.current))}
                      bot={{ status: botStatus, symbols: cfg.symbols, tz: s.tz,
                             onStart: async (name, params, syms) => { try { setBotStatus(await startBot(name, params, syms)); return null } catch (e) { return (e as Error).message } },
                             onStop: async (close) => { try { setBotStatus(await stopBot(close)); refreshTrade(); if (sheet) refreshSheet() } catch { /* shown by the next refresh */ } } }}
                      onReset={() => { if (window.confirm('Reset the paper account? This closes everything and wipes the history and equity curve.')) tradeDo('Reset', () => resetAccount()).then((ok) => ok && say('Account reset')) }} />
        )}
      </main>
      <TradeBar symbol={activeSym} disabled={!ready} peekOn={peekOn} message={msg} mode={s.mode} onMode={(m) => set({ mode: m })}
                onSubmit={onSubmit} lots={s.lots} onLots={(v) => set({ lots: v })} slPips={s.slPips} tpPips={s.tpPips}
                onSlTp={(sl, tp) => set({ slPips: sl, tpPips: tp })} orderType={orderType} onOrderType={setOrderType}
                orderPrice={orderPrice} onOrderPrice={setOrderPrice}
                bid={quote ? quote.bid.toFixed(metaOf(activeSym).digits) : '–'} ask={quote ? quote.ask.toFixed(metaOf(activeSym).digits) : '–'}
                callsOpen={panel} onToggleCalls={() => { setPanel((v) => !v); setSheet(false) }} pendingCalls={pendingCalls}
                sheetOpen={sheet} onToggleSheet={toggleSheet} openPositions={trade?.positions.length ?? 0} />
    </div>
  )
}

const EMPTY_LABELS: Label[] = []
const EMPTY_CALLS: Call[] = []
