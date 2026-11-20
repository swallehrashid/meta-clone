import { useEffect, useState } from 'react'
import { defaultsOf, parseParams, type BotInfo, type BotStatus, type ParamText } from '../lib/bot'
import { hhmmss } from '../lib/time'

export interface BotPanelProps {
  status: BotStatus | null
  symbols: string[]                  // every configured symbol
  tz: string
  onStart: (name: string, params: Record<string, unknown>, symbols: string[]) => Promise<string | null>
  onStop: (closePositions: boolean) => Promise<void>
}

export function BotPanel({ status, symbols, tz, onStart, onStop }: BotPanelProps) {
  const bots = status?.available ?? []
  const [name, setName] = useState('')
  const [text, setText] = useState<ParamText>({})
  const [syms, setSyms] = useState<string[]>([])
  const [err, setErr] = useState('')
  const info: BotInfo | undefined = bots.find((b) => b.name === name)

  useEffect(() => { if (!name && bots.length) setName(bots[0].name) }, [bots, name])
  useEffect(() => { if (info) setText(defaultsOf(info)) }, [info?.name])               // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!syms.length && symbols.length) setSyms([symbols[0]]) }, [symbols, syms.length])

  if (!status) return <div className="dim">Loading…</div>
  const st = status.stats
  const running = status.running

  const start = async () => {
    if (!info) return
    const r = parseParams(info, text)
    if (!r.ok) { setErr(r.error); return }
    if (!syms.length) { setErr('pick at least one symbol'); return }
    setErr('')
    const problem = await onStart(info.name, r.params, syms)
    if (problem) setErr(problem)
  }

  return (
    <div className="bot-panel" data-testid="bot-panel">
      <div className="dim small tip">
        The bot trades the paper account on the delayed chart, exactly as you would. It sees only data up to the chart time, never Peek data.
        Its trades are tagged <span className="tag bot-tag">bot</span> and it has hard risk limits (positions, size, daily loss).
      </div>
      {running || status.halted ? (
        <div className="pos-card" data-testid="bot-status">
          <div className="pos-head">
            <b>{status.bot?.name}</b>
            <span className={`tag ${status.halted ? 'halt-tag' : 'run-tag'}`} data-testid="bot-state">{status.halted ? 'HALTED' : 'RUNNING'}</span>
            <span className="dim small">{status.bot?.symbols.join(', ')}</span>
          </div>
          {status.halted && <div className="neg small" data-testid="bot-halt-reason">{st?.halt_reason}</div>}
          {st && (
            <div className="dim small pos-line" data-testid="bot-stats">
              <span>{st.steps} decisions</span><span>{st.opened} opened</span><span>{st.closed_by_bot} closed</span>
              <span>{st.rejected} refused</span><span>{st.errors} errors</span>
              {st.last_decision_ms ? <span>last {hhmmss(Math.floor(st.last_decision_ms / 1000), tz)}</span> : null}
            </div>
          )}
          <div className="dim small">params: {Object.entries(status.bot?.params ?? {}).map(([k, v]) => `${k}=${v}`).join(' · ')}</div>
          <div className="pos-actions">
            <button className="act" data-testid="bot-stop" onClick={() => onStop(false)}>Stop</button>
            <button className="act" data-testid="bot-stop-close" onClick={() => onStop(true)}>Stop &amp; close its trades</button>
          </div>
        </div>
      ) : (
        <div className="pos-card" data-testid="bot-form">
          <label className="field"><span>Bot</span>
            <select data-testid="bot-select" value={name} onChange={(e) => setName(e.target.value)}>{bots.map((b) => <option key={b.name} value={b.name}>{b.name}</option>)}</select>
          </label>
          {info && <div className="dim small">{info.description}</div>}
          <div className="symrow" data-testid="bot-symbols">
            {symbols.map((s) => (
              <label key={s} className={`chip${syms.includes(s) ? ' on' : ''}`}>
                <input type="checkbox" checked={syms.includes(s)} onChange={(e) => setSyms((cur) => e.target.checked ? [...cur, s] : cur.filter((x) => x !== s))} />{s}
              </label>
            ))}
          </div>
          <div className="params">
            {info && Object.entries(info.params).map(([k, spec]) => (
              <label key={k} className="field" title={spec.help}>
                <span>{k}</span>
                {spec.type === 'choice' ? (
                  <select data-testid={`bp-${k}`} value={text[k] ?? ''} onChange={(e) => setText({ ...text, [k]: e.target.value })}>{spec.choices.map((c) => <option key={c}>{c}</option>)}</select>
                ) : spec.type === 'bool' ? (
                  <input data-testid={`bp-${k}`} type="checkbox" checked={text[k] === 'true'} onChange={(e) => setText({ ...text, [k]: String(e.target.checked) })} />
                ) : (
                  <input data-testid={`bp-${k}`} inputMode="decimal" value={text[k] ?? ''} onChange={(e) => setText({ ...text, [k]: e.target.value })} />
                )}
                <small className="dim">{spec.help}</small>
              </label>
            ))}
          </div>
          {err && <div className="err small" data-testid="bot-error">{err}</div>}
          <div className="pos-actions"><button className="act primary" data-testid="bot-start" onClick={start}>Start bot</button></div>
        </div>
      )}
      <h4 className="log-h">Activity</h4>
      <ul className="botlog" data-testid="bot-log">
        {(status.log ?? []).length === 0 && <li className="dim">Nothing yet.</li>}
        {(status.log ?? []).map((e) => (
          <li key={e.id} data-kind={e.kind}>
            <span className="dim">{hhmmss(Math.floor(e.t_ms / 1000), tz)}</span>
            <span className={`tag kind-${e.kind}`}>{e.kind}</span>
            <span>{e.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
