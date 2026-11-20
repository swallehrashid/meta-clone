import { useState } from 'react'
import { formatDelay, parseDelay, TIMEZONES } from '../lib/time'
import type { Layout, Settings } from '../lib/settings'
import { HORIZONS, HORIZON_LABEL, TIMEFRAMES, type PublicConfig } from '../lib/types'

interface Props {
  cfg: PublicConfig
  s: Settings
  set: (patch: Partial<Settings>) => void
  setSlotSymbol: (sym: string) => void
  peekOn: boolean
  onPeek: () => void
  streamError: string
}

export function Toolbar({ cfg, s, set, setSlotSymbol, peekOn, onPeek, streamError }: Props) {
  const [custom, setCustom] = useState('')
  const [err, setErr] = useState('')
  const presets = cfg.delay.presets_seconds

  const submitCustom = () => {
    const sec = parseDelay(custom, cfg.delay.min_seconds, cfg.delay.max_seconds)
    if (sec === null) { setErr('use e.g. 90, 90s, 7m, 1h'); return }
    setErr(''); set({ delay: sec }); setCustom('')
  }

  return (
    <header className="toolbar" data-testid="toolbar">
      <div className="row">
        <select aria-label="Symbol" data-testid="symbol" value={s.symbols[s.active]} onChange={(e) => setSlotSymbol(e.target.value)}>
          {cfg.symbols.map((x) => <option key={x}>{x}</option>)}
        </select>
        <div className="seg" role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} data-testid={`tf-${tf}`} className={s.tf === tf ? 'on' : ''} onClick={() => set({ tf })}>{tf}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Charts">
          {([1, 2, 4] as Layout[]).map((n) => (
            <button key={n} data-testid={`layout-${n}`} className={s.layout === n ? 'on' : ''} aria-label={`${n} chart${n > 1 ? 's' : ''}`}
                    onClick={() => set({ layout: n, active: Math.min(s.active, n - 1) })}>{n}</button>
          ))}
        </div>
      </div>

      <div className="row">
        <span className="dim lbl">Delay</span>
        <div className="seg" role="group" aria-label="Delay presets">
          {presets.map((d) => (
            <button key={d} data-testid={`delay-${d}`} className={s.delay === d ? 'on' : ''} onClick={() => set({ delay: d })}>{formatDelay(d)}</button>
          ))}
        </div>
        <form className="custom" onSubmit={(e) => { e.preventDefault(); submitCustom() }}>
          <input data-testid="delay-custom" inputMode="text" placeholder={presets.includes(s.delay) ? 'custom' : formatDelay(s.delay)}
                 aria-label="Custom delay" value={custom} onChange={(e) => { setCustom(e.target.value); setErr('') }} />
          <button type="submit" data-testid="delay-set">Set</button>
        </form>
        {err && <span className="err small">{err}</span>}
      </div>

      <div className="row">
        <select aria-label="Display timezone" data-testid="tz" value={s.tz} onChange={(e) => set({ tz: e.target.value })}>
          {TIMEZONES.map((z) => <option key={z.id} value={z.id}>{z.label}</option>)}
        </select>
        <button data-testid="toggle-bid" className={`tog${s.showBid ? ' on' : ''}`} onClick={() => set({ showBid: !s.showBid })}>Bid</button>
        <button data-testid="toggle-ask" className={`tog${s.showAsk ? ' on' : ''}`} onClick={() => set({ showAsk: !s.showAsk })}>Ask</button>
        <button data-testid="toggle-labels" className={`tog${s.showLabels ? ' on' : ''}`} onClick={() => set({ showLabels: !s.showLabels })}>Labels</button>
        {s.showLabels && (
          <>
            <div className="seg" role="group" aria-label="Label direction">
              {(['buy', 'sell'] as const).map((d) => (
                <button key={d} data-testid={`ldir-${d}`} className={s.labelDir === d ? 'on' : ''} onClick={() => set({ labelDir: d })}>{d === 'buy' ? 'Buy' : 'Sell'}</button>
              ))}
            </div>
            <select aria-label="Label horizon" data-testid="lhorizon" value={s.labelHorizon} onChange={(e) => set({ labelHorizon: e.target.value as Settings['labelHorizon'] })}>
              {HORIZONS.map((h) => <option key={h} value={h}>{HORIZON_LABEL[h]}</option>)}
            </select>
          </>
        )}
        <button data-testid="toggle-peek" className={`tog peek${peekOn ? ' on' : ''}`} onClick={onPeek}
                aria-pressed={peekOn}>{peekOn ? 'Peek ON' : 'Peek'}</button>
      </div>
      {streamError && <div className="err small" data-testid="stream-error">{streamError}</div>}
    </header>
  )
}
