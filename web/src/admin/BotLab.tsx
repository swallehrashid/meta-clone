import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { EquityChart } from '../components/EquityChart'
import { defaultsOf, parseParams, type BotInfo, type ParamText } from '../lib/bot'
import { formatDelay } from '../lib/time'
import {
  AuthError, labBots, labDeleteModel, labDeleteRun, labLive, labRange, labReplay, labRun, labRuns, labStartLive, labStopLive, labTrain,
  type Evaluation, type LiveStatus, type ModelRow, type ReplayResult, type RunFull, type RunRow, type Stat, type Summary, type TrainResult,
} from './adminApi'

const f = (x: number | null | undefined, d = 2, sign = false) => (x === null || x === undefined || !isFinite(x) ? '–' : `${sign && x > 0 ? '+' : ''}${x.toFixed(d)}`)
const usd = (x: number | null | undefined) => (x === null || x === undefined ? '–' : `${x < 0 ? '−' : ''}$${Math.abs(x).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`)
const zTxt = (s?: Stat) => (s && s.z !== null && s.z !== undefined ? `z ${f(s.z, 1, true)}` : 'z –')

function usePoll<T>(fn: () => Promise<T>, ms: number, onAuth: () => void, deps: unknown[] = []): [T | null, () => void] {
  const [v, setV] = useState<T | null>(null)
  const tick = useCallback(() => { fn().then(setV).catch((e) => { if (e instanceof AuthError) onAuth() }) }, deps)     // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { tick(); const id = setInterval(tick, ms); return () => clearInterval(id) }, [tick, ms])
  return [v, tick]
}

// ---------------------------------------------------------------------------------------------------- shared form pieces
function ParamForm({ info, text, onChange, models }: { info: BotInfo; text: ParamText; onChange: (t: ParamText) => void; models: ModelRow[] }) {
  return (
    <div className="grid">
      {Object.entries(info.params).map(([k, s]) => (
        <label key={k} title={s.help}>{k}
          {s.type === 'bool' ? <select value={text[k]} onChange={(e) => onChange({ ...text, [k]: e.target.value })}><option value="true">yes</option><option value="false">no</option></select>
            : s.type === 'choice' ? <select value={text[k]} onChange={(e) => onChange({ ...text, [k]: e.target.value })}>{s.choices.map((c) => <option key={c}>{c}</option>)}</select>
            : k === 'model' ? <select value={text[k]} onChange={(e) => onChange({ ...text, [k]: e.target.value })}><option value="">— pick a trained model —</option>{models.map((m) => <option key={m.name}>{m.name}</option>)}</select>
            : <input value={text[k] ?? ''} onChange={(e) => onChange({ ...text, [k]: e.target.value })} inputMode={s.type === 'str' ? 'text' : 'decimal'} />}
          <span className="hint">{s.help}</span>
        </label>))}
    </div>
  )
}

function ForwardPicker({ value, onChange, max, needs }: { value: number; onChange: (v: number) => void; max?: number; needs?: boolean }) {
  const on = value > 0
  return (
    <div className={`fwd ${on ? 'on' : ''}`} data-testid="forward-picker">
      <div className="seg">
        <button className={!on ? 'on' : ''} disabled={needs} onClick={() => onChange(0)} data-testid="fwd-blind">Blind</button>
        <button className={on ? 'on' : ''} onClick={() => onChange(on ? value : 60)} data-testid="fwd-forward">Forward data</button>
      </div>
      {on && <label className="inline">sees the next <input type="number" min={1} max={max ?? 3600} value={value} onChange={(e) => onChange(Math.max(1, Number(e.target.value) || 1))} data-testid="fwd-seconds" /> s</label>}
      <span className="hint">{on ? 'The bot can see the candle(s) that come next. Every trade is tagged “peeked” and kept apart from blind results.' : 'The bot sees only what a person on the delayed chart sees.'}
        {needs && !on ? ' (this bot needs forward data)' : ''}</span>
    </div>
  )
}

function SymbolPick({ all, value, onChange }: { all: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return <div className="row wrap">{all.map((s) => <label key={s} className="check"><input type="checkbox" checked={value.includes(s)} onChange={() => onChange(value.includes(s) ? value.filter((x) => x !== s) : [...value, s])} /> {s}</label>)}</div>
}

const ymd = (ms: number, tz: string) => new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(ms)
const shift = (day: string, n: number) => { const d = new Date(day + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10) }

// ---------------------------------------------------------------------------------------------------- the lab
export function BotLab({ onAuth }: { onAuth: () => void }) {
  const [meta, reloadMeta] = usePoll(labBots, 15000, onAuth)
  const [range] = usePoll(labRange, 60000, onAuth)
  const [runs, reloadRuns] = usePoll(labRuns, 2000, onAuth)
  const [open, setOpen] = useState<number | null>(null)
  const tz = range?.tz ?? 'UTC'
  const last = useMemo(() => {
    const ends = Object.values(range?.symbols ?? {}).map((x) => x.to_ms)
    return ends.length ? ymd(Math.min(...ends), tz) : ymd(Date.now(), tz)
  }, [range, tz])
  if (!meta) return <div className="panel dim">Loading…</div>
  const started = (id: number) => { reloadRuns(); setOpen(id) }

  return (
    <div className="panel" data-testid="bot-lab">
      <h2>Bot lab</h2>
      <p className="dim small">Plan: <b>1)</b> watch the forward-fed <i>teacher</i> trade, <b>2)</b> learn from it, <b>3)</b> test the learned bot <b>blind</b> on days it never saw, and compare it with a random bot.
        The terminal's paper account can also be driven live, blind or with forward data.</p>
      <LivePanel meta={meta} onAuth={onAuth} />
      <ReplayPanel meta={meta} last={last} onStarted={started} />
      <TrainPanel meta={meta} last={last} onStarted={started} />
      <ModelsPanel models={meta.models} reload={reloadMeta} />
      <RunsPanel runs={runs?.runs ?? []} running={runs?.running ?? null} open={open} onOpen={setOpen} reload={reloadRuns} />
    </div>
  )
}

// ---------------------------------------------------------------------------------------------------- live
function LivePanel({ meta, onAuth }: { meta: { bots: BotInfo[]; models: ModelRow[]; symbols: string[]; delay_s: number }; onAuth: () => void }) {
  const [live, reload] = usePoll<LiveStatus>(labLive, 2000, onAuth)
  const [name, setName] = useState('forward_teacher')
  const info = meta.bots.find((b) => b.name === name) ?? meta.bots[0]
  const [text, setText] = useState<ParamText>(() => defaultsOf(info))
  const [syms, setSyms] = useState<string[]>(meta.symbols.slice(0, 1))
  const [fwd, setFwd] = useState(60)
  const [err, setErr] = useState('')
  useEffect(() => { setText(defaultsOf(info)); setFwd(info.needs_forward ? 60 : 0) }, [info.name])     // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    const p = parseParams(info, text)
    if (!p.ok) { setErr(p.error); return }
    try { await labStartLive({ bot: info.name, params: p.params, symbols: syms, forward_s: fwd }); setErr(''); reload() } catch (e) { setErr((e as Error).message) }
  }
  const stop = async (close: boolean) => { try { await labStopLive(close); reload() } catch (e) { setErr((e as Error).message) } }
  const running = live?.running

  return (
    <section className="card" data-testid="live-panel">
      <h3>Run on the terminal <span className="dim small">— trades on the paper account you see on the phone UI, at the terminal's delay ({formatDelay(meta.delay_s)})</span></h3>
      {running && live?.bot ? (
        <div>
          <div className="row"><b className="ok">● running</b> {live.bot.name} on {live.bot.symbols.join(', ')}
            {live.bot.forward_s ? <span className="tag peek">FORWARD {live.bot.forward_s}s · trades tagged peeked</span> : <span className="tag">blind</span>}
            <button onClick={() => stop(false)} data-testid="live-stop">Stop</button><button onClick={() => stop(true)}>Stop &amp; close its positions</button></div>
          <div className="dim small">decisions {live.stats?.steps ?? 0} · opened {live.stats?.opened ?? 0} · refused {live.stats?.rejected ?? 0} · errors {live.stats?.errors ?? 0}
            {live.stats?.halt_reason ? ` · HALTED: ${live.stats.halt_reason}` : ''}</div>
        </div>
      ) : (
        <div>
          <div className="row">
            <label className="inline">Bot <select value={info.name} onChange={(e) => setName(e.target.value)} data-testid="live-bot">{meta.bots.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}</select></label>
            <span className="dim small">{info.description}</span>
          </div>
          <ParamForm info={info} text={text} onChange={setText} models={meta.models} />
          <div className="row"><span className="lbl">Pairs</span><SymbolPick all={meta.symbols} value={syms} onChange={setSyms} /></div>
          <ForwardPicker value={fwd} onChange={setFwd} max={Math.floor(meta.delay_s)} needs={info.needs_forward} />
          <div className="row"><button className="primary" disabled={!syms.length} onClick={start} data-testid="live-start">Start on the terminal</button>{err && <span className="err small" data-testid="live-error">{err}</span>}</div>
          {live?.halted && <div className="err small">Last bot was halted: {live.stats?.halt_reason}</div>}
        </div>
      )}
      {live && (
        <div className="split">
          {(['clean', 'peeked'] as const).map((k) => (
            <div key={k} className={`mini ${k}`}><b>{k === 'clean' ? 'Blind (clean) trades' : 'Forward-fed (peeked) trades'}</b>
              <span>{live.deals_by_tag[k].trades} trades · {usd(live.deals_by_tag[k].net_usd)} · {f(live.deals_by_tag[k].avg_net_pips, 2, true)} net pips avg</span></div>))}
          <div className="mini"><b>Paper account</b><span>balance {usd(live.account.balance)} · equity {usd(live.account.equity)}</span></div>
        </div>
      )}
      {live && live.log.length > 0 && (
        <details><summary className="dim small">Bot activity ({live.log.length})</summary>
          <div className="log">{live.log.slice(0, 15).map((l) => <div key={l.id} className={l.kind === 'reject' || l.kind === 'error' || l.kind === 'halt' ? 'err' : ''}>{new Date(l.t_ms).toLocaleTimeString()} · {l.kind} · {l.detail}</div>)}</div></details>
      )}
    </section>
  )
}

// ---------------------------------------------------------------------------------------------------- replay
function ReplayPanel({ meta, last, onStarted }: { meta: { bots: BotInfo[]; models: ModelRow[]; symbols: string[] }; last: string; onStarted: (id: number) => void }) {
  const [name, setName] = useState('forward_teacher')
  const info = meta.bots.find((b) => b.name === name) ?? meta.bots[0]
  const [text, setText] = useState<ParamText>(() => defaultsOf(info))
  const [syms, setSyms] = useState<string[]>(meta.symbols.slice(0, 2))
  const [from, setFrom] = useState(shift(last, -1)); const [to, setTo] = useState(last)
  const [fwd, setFwd] = useState(60)
  const [err, setErr] = useState('')
  useEffect(() => { setText(defaultsOf(info)); setFwd(info.needs_forward ? 60 : 0) }, [info.name])      // eslint-disable-line react-hooks/exhaustive-deps
  const run = async () => {
    const p = parseParams(info, text)
    if (!p.ok) { setErr(p.error); return }
    try { const r = await labReplay({ bot: info.name, params: p.params, symbols: syms, date_from: from, date_to: to, forward_s: fwd }); setErr(''); onStarted(r.id) } catch (e) { setErr((e as Error).message) }
  }
  return (
    <section className="card" data-testid="replay-panel">
      <h3>Replay over stored days <span className="dim small">— fast, on a throw-away account; grade it, tweak, run again</span></h3>
      <div className="row">
        <label className="inline">Bot <select value={info.name} onChange={(e) => setName(e.target.value)} data-testid="replay-bot">{meta.bots.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}</select></label>
        <label className="inline">from <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} data-testid="replay-from" /></label>
        <label className="inline">to <input type="date" value={to} onChange={(e) => setTo(e.target.value)} data-testid="replay-to" /></label>
      </div>
      <ParamForm info={info} text={text} onChange={setText} models={meta.models} />
      <div className="row"><span className="lbl">Pairs</span><SymbolPick all={meta.symbols} value={syms} onChange={setSyms} /></div>
      <ForwardPicker value={fwd} onChange={setFwd} needs={info.needs_forward} />
      <div className="row"><button className="primary" disabled={!syms.length} onClick={run} data-testid="replay-run">Run replay</button>{err && <span className="err small" data-testid="replay-error">{err}</span>}</div>
    </section>
  )
}

// ---------------------------------------------------------------------------------------------------- train
function TrainPanel({ meta, last, onStarted }: { meta: { symbols: string[] }; last: string; onStarted: (id: number) => void }) {
  const [name, setName] = useState('m1')
  const [syms, setSyms] = useState<string[]>(meta.symbols.slice(0, 2))
  const [trFrom, setTrFrom] = useState(shift(last, -12)); const [trTo, setTrTo] = useState(shift(last, -4))
  const [teFrom, setTeFrom] = useState(shift(last, -3)); const [teTo, setTeTo] = useState(last)
  const [fwd, setFwd] = useState(60); const [hold, setHold] = useState(60); const [edge, setEdge] = useState(1)
  const [err, setErr] = useState('')
  const run = async () => {
    try {
      const r = await labTrain({ name, symbols: syms, train_from: trFrom, train_to: trTo, test_from: teFrom, test_to: teTo, forward_s: fwd, hold_s: Math.min(hold, fwd), min_edge_pips: edge })
      setErr(''); onStarted(r.id)
    } catch (e) { setErr((e as Error).message) }
  }
  return (
    <section className="card" data-testid="train-panel">
      <h3>Train a blind bot from the teacher <span className="dim small">— watch → analyse → learn → test blind</span></h3>
      <div className="grid">
        <label>Model name<input value={name} onChange={(e) => setName(e.target.value)} data-testid="train-name" /></label>
        <label>Training days from<input type="date" value={trFrom} onChange={(e) => setTrFrom(e.target.value)} data-testid="train-from" /></label>
        <label>… to<input type="date" value={trTo} onChange={(e) => setTrTo(e.target.value)} data-testid="train-to" /></label>
        <label>Blind test days from<input type="date" value={teFrom} onChange={(e) => setTeFrom(e.target.value)} data-testid="test-from" /></label>
        <label>… to<input type="date" value={teTo} onChange={(e) => setTeTo(e.target.value)} data-testid="test-to" /></label>
        <label>Teacher sees next (s)<input type="number" min={1} value={fwd} onChange={(e) => setFwd(Number(e.target.value))} data-testid="train-forward" /></label>
        <label>Hold / look-ahead (s)<input type="number" min={10} value={hold} onChange={(e) => setHold(Number(e.target.value))} /></label>
        <label>Teacher's min edge (pips after spread)<input type="number" step="0.1" min={0} value={edge} onChange={(e) => setEdge(Number(e.target.value))} /></label>
      </div>
      <div className="row"><span className="lbl">Pairs</span><SymbolPick all={meta.symbols} value={syms} onChange={setSyms} /></div>
      <p className="dim small">The test days must come after the training days. Only the blind test counts as evidence; the teacher's own numbers are an upper bound, not a result.</p>
      <div className="row"><button className="primary" disabled={!syms.length || !name} onClick={run} data-testid="train-run">Train &amp; test</button>{err && <span className="err small" data-testid="train-error">{err}</span>}</div>
    </section>
  )
}

function ModelsPanel({ models, reload }: { models: ModelRow[]; reload: () => void }) {
  if (!models.length) return null
  return (
    <section className="card"><h3>Trained models</h3>
      <table className="tbl"><thead><tr><th>name</th><th>trained on</th><th>direction AUC</th><th>auto threshold</th><th /></tr></thead><tbody>
        {models.map((m) => <tr key={m.name}><td>{m.name}</td><td>{m.trained_on} · {m.symbols?.join(', ')}</td><td>{f(m.validation_auc_direction ?? null, 3)}</td><td>{f(m.suggested_threshold ?? null, 3)}</td>
          <td><button onClick={() => { if (window.confirm(`Delete model ${m.name}?`)) labDeleteModel(m.name).then(reload) }}>delete</button></td></tr>)}
      </tbody></table></section>
  )
}

// ---------------------------------------------------------------------------------------------------- runs
function RunsPanel({ runs, running, open, onOpen, reload }: { runs: RunRow[]; running: number | null; open: number | null; onOpen: (id: number | null) => void; reload: () => void }) {
  return (
    <section className="card" data-testid="runs-panel">
      <h3>Runs {running !== null && <span className="tag peek">job #{running} running</span>}</h3>
      {!runs.length && <p className="dim small">Nothing yet. Start a replay or a training run above; every run is kept so you can compare iterations.</p>}
      <table className="tbl"><thead><tr><th>#</th><th>run</th><th>data</th><th>status</th><th>result</th><th /></tr></thead><tbody>
        {runs.map((r) => (
          <tr key={r.id} className={open === r.id ? 'sel' : ''} onClick={() => onOpen(open === r.id ? null : r.id)} data-testid={`run-${r.id}`}>
            <td>{r.id}</td><td>{r.title}</td>
            <td>{r.kind === 'train' || r.forward_s ? <span className="tag peek">{r.kind === 'train' ? `teacher ${r.forward_s}s → blind test` : `FORWARD ${r.forward_s}s`}</span> : <span className="tag">blind</span>}</td>
            <td className={r.status === 'failed' ? 'err' : ''}>{r.status === 'running' ? (r.progress ?? 'running…') : r.status === 'failed' ? `failed: ${r.error}` : 'done'}</td>
            <td className="small">{runSummary(r)}</td>
            <td><button onClick={(e) => { e.stopPropagation(); if (window.confirm(`Delete run #${r.id}?`)) labDeleteRun(r.id).then(() => { onOpen(null); reload() }) }} disabled={r.status === 'running'}>×</button></td>
          </tr>))}
      </tbody></table>
      {open !== null && <RunDetail id={open} />}
    </section>
  )
}

function runSummary(r: RunRow): string {
  const s = r.summary
  if (!s) return ''
  if (r.kind === 'replay') return `${s.trades} trades · ${f(s.avg_net_pips as number, 2, true)} pips/trade · ${usd(s.net_usd as number)} · direction ${s.direction_z === null || s.direction_z === undefined ? 'z –' : 'z ' + f(s.direction_z as number, 1, true)}`
  return `blind ${f(s.blind_avg_net_pips as number, 2, true)} · random ${f(s.random_avg_net_pips as number, 2, true)} · teacher ${f(s.teacher_avg_net_pips as number, 2, true)} pips/trade · direction AUC ${f(s.direction_auc as number, 3)}`
}

function RunDetail({ id }: { id: number }) {
  const [run, setRun] = useState<RunFull | null>(null)
  const alive = useRef(true)
  const finished = useRef(false)        // a ref, not state: the interval below must see the CURRENT job's status, not the previous run's
  useEffect(() => {
    alive.current = true; finished.current = false; setRun(null)
    const load = () => labRun(id).then((r) => { if (alive.current) { finished.current = r.status !== 'running'; setRun(r) } }).catch(() => undefined)
    load()
    const t = setInterval(() => { if (!finished.current) load() }, 2000)
    return () => { alive.current = false; clearInterval(t) }
  }, [id])
  if (!run) return <div className="dim small">Loading…</div>
  if (run.status === 'running') return <div className="detail dim">Running… {run.progress}</div>
  if (run.status === 'failed' || !run.result) return <div className="detail err">Failed: {run.error}</div>
  return <div className="detail" data-testid="run-detail">{run.kind === 'replay' ? <ReplayDetail r={run.result as ReplayResult} /> : <TrainDetail r={run.result as TrainResult} />}</div>
}

function Stats({ s }: { s: Summary }) {
  return (
    <table className="tbl kv"><tbody>
      <tr><td>Trades</td><td>{s.trades}</td><td>Net</td><td>{usd(s.net_usd)} ({f(s.return_pct, 2, true)}%)</td></tr>
      <tr><td>Avg net pips / trade</td><td>{f(s.avg_net_pips, 2, true)}</td><td>Win rate</td><td>{f(s.win_pct, 1)}%</td></tr>
      <tr><td>Profit factor</td><td>{f(s.profit_factor, 2)}</td><td>Max drawdown</td><td>{f(s.max_drawdown_pct, 2)}%</td></tr>
      <tr><td>Avg hold</td><td>{f(s.avg_hold_s, 0)} s</td><td>Exits</td><td>{Object.entries(s.exit_reasons).map(([k, n]) => `${k} ${n}`).join(' · ') || '–'}</td></tr>
    </tbody></table>
  )
}

function Edges({ ev }: { ev: Evaluation }) {
  if (!ev.direction_edge_pips) return <p className="dim small">{ev.matching?.note ?? 'No grading against labels (no matched trades).'}</p>
  return (
    <table className="tbl kv"><tbody>
      <tr><td title="bot's side minus the opposite side of the same entry: 0 for a coin flip">Direction edge</td><td>{f(ev.direction_edge_pips.mean, 2, true)} pips ({zTxt(ev.direction_edge_pips)})</td>
        <td title="selection edge with the entry spread added back">Selection edge ex-spread</td><td>{f(ev.selection_edge_ex_spread_pips?.mean, 2, true)} pips ({zTxt(ev.selection_edge_ex_spread_pips)})</td></tr>
      {ev.oracle && <tr><td>Oracle lift</td><td>{f(ev.oracle.lift, 2)} ({f(ev.oracle.bot_rate_pct, 1)}% vs {f(ev.oracle.base_rate_pct, 1)}% base)</td><td /><td /></tr>}
    </tbody></table>
  )
}

function ReplayDetail({ r }: { r: ReplayResult }) {
  return (
    <>
      {r.tagged_peeked && <div className="banner peek">FORWARD-FED RUN — the bot saw the next {r.forward_s}s. Its trades are tagged <b>peeked</b>. This shows what perfect hindsight would do; it is not evidence of any performance.</div>}
      <Stats s={r.evaluation.summary} />
      <h4>Against the labels and the oracle</h4><Edges ev={r.evaluation} />
      <p className="dim small">z-scores are cluster-robust (trades in the same market hour count once). On simulated data a correct bot finds no edge — a replay is a test of the harness, not of a market.</p>
      <EquityChart points={r.equity} tz="UTC" />
      <details><summary className="dim small">Last {Math.min(50, r.deals.length)} trades</summary>
        <table className="tbl"><thead><tr><th>pair</th><th>side</th><th>lots</th><th>opened (UTC)</th><th>net pips</th><th>net $</th><th>exit</th></tr></thead><tbody>
          {r.deals.slice(-50).reverse().map((d) => <tr key={d.id}><td>{d.symbol}</td><td>{d.side}</td><td>{d.volume}</td><td>{new Date(d.open_ms).toISOString().slice(5, 16).replace('T', ' ')}</td><td>{f(d.net_pips, 1, true)}</td><td>{f(d.net, 2, true)}</td><td>{d.reason}</td></tr>)}
        </tbody></table></details>
    </>
  )
}

function TrainDetail({ r }: { r: TrainResult }) {
  const t = r.test
  const rows: [string, string][] = [['learned_blind', 'Learned bot — BLIND'], ['random_null', 'Random bot (null benchmark)'], ['teacher_forward', 'Teacher — forward data (upper bound)']]
  return (
    <>
      {r.verdict && <div className="banner verdict" data-testid="verdict">{r.verdict}</div>}
      <h4>1. What the teacher did</h4>
      <p className="small">{r.analysis.decisions.toLocaleString()} decision minutes · teacher bought {f(r.analysis.teacher_buy_pct, 1)}%, sold {f(r.analysis.teacher_sell_pct, 1)}%, stood aside the rest · {f(r.analysis.teacher_avg_net_pips_when_trading, 2, true)} net pips per trade <span className="dim">(with hindsight)</span></p>
      <h4>2. What the features say about its choices</h4>
      <table className="tbl"><thead><tr><th>feature</th><th>corr. with “trade at all”</th><th>corr. with “buy (+) vs sell (−)”</th></tr></thead><tbody>
        {r.analysis.features.slice(0, 8).map((x) => <tr key={x.feature}><td>{x.feature}</td><td>{f(x.corr_trade, 3, true)}</td><td>{f(x.corr_direction, 3, true)}</td></tr>)}</tbody></table>
      <h4>3. How well a model learned it (validation days, never fitted)</h4>
      <p className="small">trade / no-trade AUC <b>{f(r.fit.validation_auc_trade, 3)}</b> <span className="dim">(volatility is predictable)</span> · buy-vs-sell <b>direction</b> AUC <b>{f(r.fit.validation_auc_direction, 3)}</b> <span className="dim">(0.5 = no skill; this is the one that matters)</span> · auto threshold {f(r.fit.suggested_threshold, 3)}</p>
      {t && (<>
        <h4>4. Blind test on days after the training period</h4>
        <table className="tbl"><thead><tr><th /><th>trades</th><th>avg net pips</th><th>net $</th><th>direction edge</th><th>sel. edge ex-spread</th></tr></thead><tbody>
          {rows.map(([k, label]) => { const x = t[k]; return x && (
            <tr key={k} className={k === 'learned_blind' ? 'sel' : ''}><td>{label}{x.forward_s ? <span className="tag peek">peeked</span> : null}</td><td>{x.summary.trades}</td><td>{f(x.summary.avg_net_pips, 2, true)}</td><td>{usd(x.summary.net_usd)}</td>
              <td>{x.direction_edge_pips ? `${f(x.direction_edge_pips.mean, 2, true)} (${zTxt(x.direction_edge_pips)})` : '–'}</td><td>{x.selection_edge_ex_spread_pips ? `${f(x.selection_edge_ex_spread_pips.mean, 2, true)} (${zTxt(x.selection_edge_ex_spread_pips)})` : '–'}</td></tr>) })}
        </tbody></table>
        <p className="dim small">Judge the blind bot only against the random bot. The teacher row shows what forward data is worth — none of it is the blind bot's.</p>
      </>)}
    </>
  )
}

