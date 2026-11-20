import { describe, expect, it } from 'vitest'
import { mergeBars, trimBars } from './series'
import type { Bar } from './types'

const b = (time: number, c: number): Bar => ({ time, open: 1, high: 2, low: 0.5, close: c })

describe('mergeBars', () => {
  it('appends newer bars and replaces the forming one', () => {
    const bars = [b(60, 1), b(120, 2)]
    const applied = mergeBars(bars, [b(120, 3), b(180, 4)])
    expect(bars.map((x) => [x.time, x.close])).toEqual([[60, 1], [120, 3], [180, 4]])
    expect(applied.map((x) => x.time)).toEqual([120, 180])
  })
  it('splices an older bar in order and replaces an exact older match', () => {
    const bars = [b(60, 1), b(180, 3)]
    mergeBars(bars, [b(120, 2)])
    expect(bars.map((x) => x.time)).toEqual([60, 120, 180])
    mergeBars(bars, [b(60, 9)])
    expect(bars[0].close).toBe(9)
  })
  it('works from empty and never aliases the caller objects', () => {
    const bars: Bar[] = []
    const inc = b(60, 1)
    mergeBars(bars, [inc])
    inc.close = 99
    expect(bars[0].close).toBe(1)
  })
  it('trimBars keeps the newest', () => {
    const bars = [b(1, 1), b(2, 2), b(3, 3), b(4, 4)]
    trimBars(bars, 2)
    expect(bars.map((x) => x.time)).toEqual([3, 4])
  })
})
