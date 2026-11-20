import { describe, expect, it } from 'vitest'
import { fmtMoney, fmtPips, halfLots, levelFromPips, parseLots, pipsFrom, stepLots } from './tradeMath'
import { dragPrice, hitLevel, levelsOf } from './levels'

describe('levelFromPips', () => {
  it('puts SL below and TP above for a buy, mirrored for a sell, on the right pip size', () => {
    expect(levelFromPips('buy', 'sl', 1.1000, 5, 1e-4, 5)).toBe(1.0995)
    expect(levelFromPips('buy', 'tp', 1.1000, 8, 1e-4, 5)).toBe(1.1008)
    expect(levelFromPips('sell', 'sl', 1.1000, 5, 1e-4, 5)).toBe(1.1005)
    expect(levelFromPips('sell', 'tp', 1.1000, 8, 1e-4, 5)).toBe(1.0992)
    expect(levelFromPips('buy', 'sl', 162.500, 10, 0.01, 3)).toBe(162.4)         // JPY pairs: pip = 0.01
  })
  it('round-trips with pipsFrom', () => {
    expect(pipsFrom(1.1000, levelFromPips('buy', 'tp', 1.1, 12.5, 1e-4, 5), 1e-4)).toBeCloseTo(12.5, 6)
  })
})

describe('lots', () => {
  it('steps sensibly and stays in range', () => {
    expect(stepLots(0.01, 1)).toBe(0.02)
    expect(stepLots(0.1, 1)).toBe(0.2)
    expect(stepLots(1, 1)).toBe(1.5)
    expect(stepLots(0.01, -1)).toBe(0.01)
    expect(stepLots(50, 1)).toBe(50)
    expect(stepLots(0.3, -1)).toBe(0.2)
  })
  it('parses typed lots, snapping to the step and rejecting nonsense', () => {
    expect(parseLots('0.5')).toBe(0.5)
    expect(parseLots('0,25')).toBe(0.25)
    expect(parseLots('0.333')).toBe(0.33)
    for (const bad of ['', 'abc', '0', '-1', '0.004', '51']) expect(parseLots(bad)).toBeNull()
  })
  it('halves a position into valid sizes only', () => {
    expect(halfLots(1)).toBe(0.5)
    expect(halfLots(0.03)).toBe(0.01)
    expect(halfLots(0.01)).toBeNull()
  })
})

describe('formatting', () => {
  it('money and pips carry signs', () => {
    expect(fmtMoney(1234.5)).toBe('1,234.50')
    expect(fmtMoney(-12.3, true)).toBe('−12.30')
    expect(fmtMoney(5, true)).toBe('+5.00')
    expect(fmtMoney(null)).toBe('–')
    expect(fmtPips(3.14)).toBe('+3.1')
    expect(fmtPips(-0.04)).toBe('−0.0')
  })
})

describe('level hit-testing and dragging', () => {
  const toY = (p: number) => (1.2 - p) * 10000           // 1 pip = 1 px, higher price = higher on screen = smaller y
  const levels = levelsOf([{ id: 1, sl: 1.0990, tp: 1.1020 }, { id: 2, sl: null, tp: 1.1010 }],
                          [{ id: 7, price: 1.0950, sl: null, tp: null }])
  it('lists every SL / TP / pending price with stable keys', () => {
    expect(levels.map((l) => l.key)).toEqual(['p1:sl', 'p1:tp', 'p2:tp', 'o7:price'])
  })
  it('picks the nearest line within tolerance, otherwise nothing', () => {
    expect(hitLevel(levels, toY(1.0990) + 5, toY)?.key).toBe('p1:sl')
    expect(hitLevel(levels, toY(1.1020) - 3, toY)?.key).toBe('p1:tp')
    expect(hitLevel(levels, toY(1.1015), toY, 4)).toBeNull()                      // 5 px from both 1.1010 and 1.1020 at tol 4
    expect(hitLevel(levels, toY(1.1015), toY, 14)?.field).toBe('tp')
    expect(hitLevel([], 10, toY)).toBeNull()
  })
  it('when two lines are close the closer one wins', () => {
    const l2 = levelsOf([{ id: 1, sl: 1.0990, tp: null }, { id: 2, sl: 1.0993, tp: null }], [])
    expect(hitLevel(l2, toY(1.0992), toY)?.key).toBe('p2:sl')
  })
  it('converts a pointer row back to a snapped price', () => {
    const toPrice = (y: number) => 1.2 - y / 10000
    expect(dragPrice(toY(1.09873) , toPrice, 4)).toBe(1.0987)
    expect(dragPrice(5, () => null, 5)).toBeNull()
  })
  it('never lets a position\'s own entry price be dragged', () => {
    expect(hitLevel([{ key: 'p1:price', id: 1, kind: 'position', field: 'price', price: 1.1 }], toY(1.1), toY)).toBeNull()
  })
})
