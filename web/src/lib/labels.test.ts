import { describe, expect, it } from 'vitest'
import { LabelsCache } from './labels'
import type { Label } from './types'

const L = (min: number, dir: 'buy' | 'sell' = 'buy'): Label => ({
  symbol: 'EURUSD', entry_t_ms: min * 60_000, direction: dir, status: 'final', entry_price: 1, spread_pips: 1,
  pnl_30s: 1, pnl_60s: 1, pnl_120s: 1, pnl_300s: 1, mfe_pips: 0, mae_pips: 0,
})

describe('LabelsCache', () => {
  it('first load is full, later refreshes only re-read the recent tail', async () => {
    const calls: number[] = []
    const all = [L(0), L(1), L(2), L(30), L(31)]
    const c = new LabelsCache('EURUSD', async (q) => { calls.push(q.tFrom); return { labels: all.filter((l) => l.entry_t_ms >= q.tFrom) } })
    await c.refresh(0, 300)
    expect(c.labels.length).toBe(5)
    all.push(L(32))
    await c.refresh(0, 300)
    expect(calls[1]).toBe(31 * 60_000 - LabelsCache.REFRESH_BACK_MS)       // tail only, not from 0
    expect(c.labels.length).toBe(6)
  })
  it('replaces entries that changed, no duplicates', async () => {
    const data = [L(0)]
    const c = new LabelsCache('EURUSD', async () => ({ labels: data.map((l) => ({ ...l })) }))
    await c.refresh(0, 300)
    data[0].pnl_60s = 7
    await c.refresh(0, 300)
    expect(c.labels).toHaveLength(1); expect(c.labels[0].pnl_60s).toBe(7)
  })
  it('a delay change or a wider range reloads everything', async () => {
    const calls: [number, number][] = []
    const c = new LabelsCache('EURUSD', async (q) => { calls.push([q.tFrom, q.delay]); return { labels: [L(5)] } })
    await c.refresh(100, 300); await c.refresh(100, 60); await c.refresh(50, 60); await c.refresh(60, 60)
    expect(calls).toEqual([[100, 300], [100, 60], [50, 60], [60, 60]])
  })
  it('drops an answer that was superseded while in flight', async () => {
    let release!: (v: { labels: Label[] }) => void
    let first = true
    const c = new LabelsCache('EURUSD', () => first ? (first = false, new Promise((r) => { release = r })) : Promise.resolve({ labels: [L(9)] }))
    const slow = c.refresh(0, 300)                  // old delay, still in flight
    await c.refresh(0, 60)                          // user switched delay; this finishes first
    release({ labels: [L(1), L(2)] })               // stale answer arrives late
    await slow
    expect(c.labels.map((l) => l.entry_t_ms)).toEqual([9 * 60_000])
  })
})
