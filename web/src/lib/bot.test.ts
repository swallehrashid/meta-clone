import { describe, expect, it } from 'vitest'
import { defaultsOf, parseParams, type BotInfo } from './bot'

const info: BotInfo = {
  name: 'demo', description: '', interval_ms: 60000,
  params: {
    p_enter: { type: 'float', default: 0.02, min: 0, max: 1, choices: [], help: '' },
    hold_s: { type: 'int', default: 120, min: 10, max: 3600, choices: [], help: '' },
    side: { type: 'choice', default: 'both', min: null, max: null, choices: ['both', 'buy', 'sell'], help: '' },
    reverse: { type: 'bool', default: false, min: null, max: null, choices: [], help: '' },
  },
}

describe('bot parameter form', () => {
  it('starts from the declared defaults', () => {
    expect(defaultsOf(info)).toEqual({ p_enter: '0.02', hold_s: '120', side: 'both', reverse: 'false' })
  })
  it('parses valid text into typed values', () => {
    const r = parseParams(info, { p_enter: '0.5', hold_s: '90', side: 'buy', reverse: 'true' })
    expect(r).toEqual({ ok: true, params: { p_enter: 0.5, hold_s: 90, side: 'buy', reverse: true } })
  })
  it('names the first problem for each kind of mistake', () => {
    const base = defaultsOf(info)
    const err = (patch: Record<string, string>) => { const r = parseParams(info, { ...base, ...patch }); return r.ok ? null : r.error }
    expect(err({ p_enter: '' })).toMatch(/p_enter.*number/)
    expect(err({ p_enter: 'abc' })).toMatch(/p_enter.*number/)
    expect(err({ p_enter: '1.5' })).toMatch(/p_enter.*at most 1/)
    expect(err({ hold_s: '5' })).toMatch(/hold_s.*at least 10/)
    expect(err({ hold_s: '90.5' })).toMatch(/whole number/)
    expect(err({ side: 'up' })).toMatch(/side.*both, buy, sell/)
    expect(err({})).toBeNull()
  })
})
