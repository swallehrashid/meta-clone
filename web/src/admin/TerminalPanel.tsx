import { useEffect, useState } from 'react'
import { getAdminTerminal, putAdminTerminal, type TerminalSettings } from '../lib/api'
import { formatDelay, parseDelay } from '../lib/time'
import type { Timeframe } from '../lib/types'
import { AuthError, resetPaperAccount } from './adminApi'

const ZONES = ['America/New_York', 'UTC', 'Europe/London', 'Europe/Athens', 'Europe/Berlin', 'Asia/Tokyo', 'Australia/Sydney']
const PRESETS = [60, 120, 300, 900, 3600]

/** Everything the phone-style terminal obeys. It has no settings screen of its own: change it here and it follows within seconds. */
export function TerminalPanel({ onAuth }: { onAuth: () => void }) {
  const [s, setS] = useState<TerminalSettings | null>(null)
  const [draft, setDraft] = useState<Partial<TerminalSettings>>({})
  const [delayText, setDelayText] = useState('')
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null)

  const load = () => getAdminTerminal().then((t) => { setS(t); setDraft({}); setDelayText(formatDelay(t.delay_s)) }).catch((e) => (e instanceof AuthError || /401/.test(String(e)) ? onAuth() : setMsg({ text: String(e.message ?? e), bad: true })))
  useEffect(() => { load() }, [])        // eslint-disable-line react-hooks/exhaustive-deps
  if (!s) return <div className="panel dim">Loading…</div>

  const v = <K extends keyof TerminalSettings>(k: K): TerminalSettings[K] => (k in draft ? draft[k] : s[k]) as TerminalSettings[K]
  const set = (patch: Partial<TerminalSettings>) => setDraft((d) => ({ ...d, ...patch }))
  const dirty = Object.keys(draft).length > 0
  const delay = parseDelay(delayText, s.delay.min, s.delay.max)

  const save = async () => {
    const patch = { ...draft }
    if (delay !== null && delay !== s.delay_s) patch.delay_s = delay
    try { const t = await putAdminTerminal(patch); setS(t); setDraft({}); setDelayText(formatDelay(t.delay_s)); setMsg({ text: 'Saved — the terminal follows within a few seconds.' }) }
    catch (e) { setMsg({ text: (e as Error).message, bad: true }) }
  }
  const toggleSymbol = (sym: string) => {
    const cur = v('symbols')
    set({ symbols: cur.includes(sym) ? cur.filter((x) => x !== sym) : [...cur, sym] })
  }
  const changed = dirty || (delay !== null && delay !== s.delay_s)

  return (
    <div className="panel" data-testid="terminal-panel">
      <h2>Terminal</h2>
      <p className="dim small">The public terminal at <code>/</code> is a plain MetaTrader 5 look-alike. Everything about <em>how</em> it behaves is decided here.</p>

      <section className="card">
        <h3>Delay</h3>
        <p className="dim small">How far behind real time the chart, the quotes and the paper account run. The terminal never receives anything newer.</p>
        <div className="row">
          {PRESETS.map((p) => <button key={p} className={delay === p ? 'on' : ''} onClick={() => setDelayText(formatDelay(p))}>{formatDelay(p)}</button>)}
          <input value={delayText} onChange={(e) => setDelayText(e.target.value)} placeholder="90s, 7m, 1h" aria-label="Delay" data-testid="delay-input" />
          {delay === null && <span className="err small">between {formatDelay(s.delay.min)} and {formatDelay(s.delay.max)}</span>}
        </div>
        <p className="dim small">Now: <b>{formatDelay(s.delay_s)}</b>. Lengthening the delay pauses trading until the chart catches up with the paper account's clock.</p>
      </section>

      <section className="card">
        <h3>Chart &amp; trading</h3>
        <div className="grid">
          <label>Default timeframe
            <select value={v('tf')} onChange={(e) => set({ tf: e.target.value as Timeframe })}>{s.timeframes.map((t) => <option key={t}>{t}</option>)}</select></label>
          <label>One-click lot size
            <input type="number" step="0.01" min="0.01" value={v('volume')} onChange={(e) => set({ volume: Number(e.target.value) })} /></label>
          <label>Server time zone (shown on every time)
            <select value={v('tz')} onChange={(e) => set({ tz: e.target.value })}>{[...new Set([...ZONES, v('tz')])].map((z) => <option key={z}>{z}</option>)}</select></label>
          <label className="check"><input type="checkbox" checked={v('show_ask_line')} onChange={(e) => set({ show_ask_line: e.target.checked })} /> Show the Ask line on the chart (the Bid line is always shown)</label>
        </div>
      </section>

      <section className="card">
        <h3>Quoted pairs</h3>
        <div className="row wrap">{(s.all_symbols ?? s.symbols).map((sym) => (
          <label key={sym} className="check"><input type="checkbox" checked={v('symbols').includes(sym)} onChange={() => toggleSymbol(sym)} /> {sym}</label>))}</div>
      </section>

      <section className="card">
        <h3>Account label (cosmetic)</h3>
        <div className="grid">
          <label>Name<input value={v('account_name')} onChange={(e) => set({ account_name: e.target.value })} /></label>
          <label>Login<input type="number" value={v('login')} onChange={(e) => set({ login: Number(e.target.value) })} /></label>
          <label>Server<input value={v('server_name')} onChange={(e) => set({ server_name: e.target.value })} /></label>
        </div>
      </section>

      <div className="row">
        <button className="primary" disabled={!changed || (delayText !== '' && delay === null) || v('symbols').length === 0} onClick={save} data-testid="save-terminal">Save</button>
        {changed && <button onClick={load}>Discard</button>}
        {msg && <span className={msg.bad ? 'err' : 'ok'} data-testid="terminal-msg">{msg.text}</span>}
      </div>

      <section className="card danger">
        <h3>Paper account</h3>
        <p className="dim small">Closes every position and wipes the history and equity curve. Cannot be undone.</p>
        <button onClick={() => { if (window.confirm('Reset the paper account? This wipes all positions, history and the equity curve.')) resetPaperAccount().then(() => setMsg({ text: 'Account reset.' })).catch((e) => setMsg({ text: String(e.message ?? e), bad: true })) }}>Reset paper account…</button>
      </section>
    </div>
  )
}
