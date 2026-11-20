import { hhmmss } from '../lib/time'
import { HORIZONS, HORIZON_LABEL, type Call, type ScoreGroup, type Scorecard } from '../lib/types'

const pips = (v: number | null | undefined) => (v === null || v === undefined ? '–' : (v > 0 ? '+' : '') + v.toFixed(1))
const cls = (v: number | null | undefined) => (v === null || v === undefined ? '' : v > 0 ? 'pos' : v < 0 ? 'neg' : '')

function Group({ title, g, testId }: { title: string; g: ScoreGroup; testId: string }) {
  return (
    <div className="score" data-testid={testId}>
      <div className="score-title">
        <b>{title}</b>
        <span className="dim small" data-testid={`${testId}-counts`}>{g.calls} {g.calls === 1 ? 'call' : 'calls'} · {g.graded} graded{g.pending ? ` · ${g.pending} pending` : ''}</span>
      </div>
      <table>
        <thead><tr><th></th>{HORIZONS.map((h) => <th key={h}>{HORIZON_LABEL[h]}</th>)}</tr></thead>
        <tbody>
          <tr><td className="dim">avg pips</td>{HORIZONS.map((h) => <td key={h} className={cls(g.avg_pips[h])}>{pips(g.avg_pips[h])}</td>)}</tr>
          <tr><td className="dim">win %</td>{HORIZONS.map((h) => <td key={h}>{g.win_rate[h] === null ? '–' : g.win_rate[h]!.toFixed(0)}</td>)}</tr>
        </tbody>
      </table>
    </div>
  )
}

interface Props { calls: Call[]; score: Scorecard | null; tz: string; onClose: () => void }

export function CallsPanel({ calls, score, tz, onClose }: Props) {
  return (
    <section className="sheet" data-testid="calls-panel">
      <div className="sheet-head">
        <b>Practice calls</b>
        <a className="report-link" data-testid="report-link" href="/report" target="_blank" rel="noreferrer">Baseline &amp; feature report ↗</a>
        <button onClick={onClose} aria-label="Close calls">✕</button>
      </div>
      <div className="sheet-body">
        {score && (
          <div className="scores">
            <Group title="Clean" g={score.clean} testId="score-clean" />
            <Group title="Peeked" g={score.peeked} testId="score-peeked" />
          </div>
        )}
        <div className="dim small tip">Net of spread (entries at ask for buys, bid for sells). Outcome = {HORIZON_LABEL.pnl_300s} window after the call.</div>
        <ul className="calls" data-testid="calls-list">
          {calls.length === 0 && <li className="dim">No calls yet – tap BUY or SELL.</li>}
          {calls.map((c) => (
            <li key={c.id} data-testid="call-row" data-status={c.status} data-peeked={c.peeked}>
              <span className="dim">{hhmmss(Math.floor(c.t_ms / 1000), tz)}</span>
              <span>{c.symbol}</span>
              <span className={c.direction === 'buy' ? 'buy-text' : 'sell-text'}>{c.direction.toUpperCase()}</span>
              {c.peeked ? <span className="tag peek-tag">peeked</span> : <span />}
              {c.status === 'final'
                ? (
                  <span className="pnls" data-testid="call-pnl">
                    {HORIZONS.map((h) => <span key={h} className={cls(c[h])}><i>{HORIZON_LABEL[h]}</i> {pips(c[h])}</span>)}
                  </span>
                )
                : <span className="pnls dim">{c.status === 'ungradable' ? 'no fill (market closed)' : 'grading…'}</span>}
            </li>
          ))}
        </ul>
        <div className="dim small attrib">
          Charts by <a href="https://www.tradingview.com/" target="_blank" rel="noreferrer">TradingView</a> Lightweight Charts™
        </div>
      </div>
    </section>
  )
}
