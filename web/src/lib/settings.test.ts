import { describe, expect, it } from 'vitest'
import { defaults, newSessionId, sanitize, visibleSymbols } from './settings'
import type { PublicConfig } from './types'

const cfg: PublicConfig = {
  symbols: ['EURUSD', 'GBPUSD', 'EURJPY', 'GBPJPY'], timeframes: ['M1', 'M5', 'M15', 'H1'],
  delay: { default_seconds: 300, presets_seconds: [60, 300, 900], min_seconds: 1, max_seconds: 86400 },
  display_timezone: 'America/New_York', price_side: 'bid',
}

describe('settings', () => {
  it('defaults come from the server config; labels and ask line are off', () => {
    const d = defaults(cfg)
    expect(d).toMatchObject({ layout: 1, tf: 'M1', delay: 300, tz: 'America/New_York', showLabels: false, showAsk: false, showBid: true })
    expect(d.symbols).toEqual(cfg.symbols)
  })
  it('keeps valid stored values and discards invalid ones', () => {
    const s = sanitize({ symbols: ['GBPJPY', 'NOPE', 'EURJPY', 'EURUSD'], layout: 4, tf: 'H1', delay: 900, tz: 'UTC', active: 2 }, cfg)
    expect(s.symbols).toEqual(['GBPJPY', 'GBPUSD', 'EURJPY', 'EURUSD'])
    expect([s.layout, s.tf, s.delay, s.tz, s.active]).toEqual([4, 'H1', 900, 'UTC', 2])
    const bad = sanitize({ layout: 3, tf: 'W1', delay: 0, active: 9 }, cfg)
    expect([bad.layout, bad.tf, bad.delay, bad.active]).toEqual([1, 'M1', 300, 0])
    expect(sanitize('garbage', cfg)).toEqual(defaults(cfg))
    expect(sanitize(null, cfg)).toEqual(defaults(cfg))
  })
  it('never restores the labels overlay (practice starts clean)', () => {
    expect(sanitize({ showLabels: true }, cfg).showLabels).toBe(false)
  })
  it('trade settings: defaults to practice mode and validates lots / SL / TP', () => {
    const d = defaults(cfg)
    expect([d.mode, d.lots, d.slPips, d.tpPips]).toEqual(['practice', 0.1, null, null])
    const s = sanitize({ mode: 'trade', lots: 0.75, slPips: 8, tpPips: 12 }, cfg)
    expect([s.mode, s.lots, s.slPips, s.tpPips]).toEqual(['trade', 0.75, 8, 12])
    const bad = sanitize({ mode: 'yolo', lots: 500, slPips: -3, tpPips: 'x' }, cfg)
    expect([bad.mode, bad.lots, bad.slPips, bad.tpPips]).toEqual(['practice', 0.1, null, null])
  })
  it('visibleSymbols follows the layout and de-duplicates', () => {
    const s = defaults(cfg)
    expect(visibleSymbols(s)).toEqual(['EURUSD'])
    expect(visibleSymbols({ ...s, layout: 2 })).toEqual(['EURUSD', 'GBPUSD'])
    expect(visibleSymbols({ ...s, layout: 4, symbols: ['EURUSD', 'EURUSD', 'GBPUSD', 'GBPUSD'] })).toEqual(['EURUSD', 'GBPUSD'])
  })
  it('session ids work without a secure context and are unique', () => {
    const ids = new Set(Array.from({ length: 50 }, newSessionId))
    expect(ids.size).toBe(50)
    for (const id of ids) expect(id).toMatch(/^[0-9a-f-]{32,36}$/)
  })
})
