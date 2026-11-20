import { describe, expect, it } from 'vitest'
import { countdown, dayMonHm, hms, money, priceParts, signed, stamp } from './format'

describe('MT5 price formatting', () => {
  it('splits a 5-digit price into small prefix, two big digits and a superscript last digit', () => {
    expect(priceParts(1.22456, 5)).toEqual({ pre: '1.22', big: '45', sup: '6' })
    expect(priceParts(1.0375, 5)).toEqual({ pre: '1.03', big: '75', sup: '0' })        // trailing zero is kept, not dropped
  })
  it('handles 3-digit JPY prices', () => {
    expect(priceParts(107.58, 3)).toEqual({ pre: '107.', big: '58', sup: '0' })
    expect(priceParts(190.088, 3)).toEqual({ pre: '190.', big: '08', sup: '8' })
  })
  it('never throws on a tiny string', () => { expect(priceParts(5, 0)).toEqual({ pre: '', big: '5', sup: '' }) })
})

describe('money, signs and countdown', () => {
  it('prints thousands with a thin space and two decimals', () => {
    expect(money(9884.69)).toBe('9 884.69')
    expect(money(10000)).toBe('10 000.00')
    expect(money(-100.31)).toBe('-100.31')
    expect(money(1234567.891)).toBe('1 234 567.89')
  })
  it('does not print a minus for a value that rounds to zero', () => { expect(money(-0.001)).toBe('0.00') })
  it('shows dashes for missing numbers', () => { expect(money(null)).toBe('–'); expect(money(undefined)).toBe('–'); expect(money(NaN)).toBe('–') })
  it('signs points and percentages', () => {
    expect(signed(-185)).toBe('-185'); expect(signed(14)).toBe('+14'); expect(signed(0)).toBe('0'); expect(signed(0.02, 2)).toBe('+0.02')
  })
  it('counts down like the candle timer', () => {
    expect(countdown(19 * 60 + 14)).toBe('19:14'); expect(countdown(5)).toBe('00:05'); expect(countdown(3661)).toBe('1:01:01')
    expect(countdown(-3)).toBe('00:00')                                                 // never negative
  })
})

describe('server-time formatting', () => {
  const t = Date.UTC(2026, 10, 10, 16, 13, 54)                                         // 2026-11-10 16:13:54 UTC
  it('prints the History timestamp in the chosen zone', () => {
    expect(stamp(t, 'UTC')).toBe('2026.11.10 16:13:54')
    expect(stamp(t, 'America/New_York')).toBe('2026.11.10 11:13:54')                    // EST in November
    expect(stamp(t, 'Asia/Tokyo')).toBe('2026.11.11 01:13:54')                          // crosses midnight
  })
  it('prints the quote time and the chart axis label', () => {
    expect(hms(t, 'UTC')).toBe('16:13:54')
    expect(dayMonHm(t / 1000, 'UTC')).toBe('10 Nov 16:13')
  })
})

import { crossTime, dmy, vol, duration } from './format'
describe('MT5 labels', () => {
  const t = Date.UTC(2026, 10, 17, 5, 25, 0)
  it('crosshair and period formats', () => {
    expect(crossTime(t / 1000, 'UTC')).toBe("Tue 17 Nov '26 05:25")
    expect(dmy(t, 'UTC')).toBe('17/11/2026')
    expect(vol(0.03)).toBe('0.03'); expect(vol(0.1)).toBe('0.1'); expect(vol(1)).toBe('1')
    expect(duration(3725)).toBe('1h 2m'); expect(duration(90000)).toBe('1d 1h')
  })
})
