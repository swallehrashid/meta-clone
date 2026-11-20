import { describe, expect, it } from 'vitest'
import { formatDelay, fullTime, hhmm, hhmmss, parseDelay, tickMark } from './time'

const utc = (iso: string) => Date.parse(iso) / 1000

describe('timezone display', () => {
  it('renders the same instant in the chosen zone, DST-aware', () => {
    const winter = utc('2025-03-04T14:05:07Z')      // New York is EST (UTC-5)
    const summer = utc('2025-03-10T14:05:07Z')      // EDT (UTC-4)
    expect(hhmm(winter, 'America/New_York')).toBe('09:05')
    expect(hhmm(summer, 'America/New_York')).toBe('10:05')
    expect(hhmm(winter, 'UTC')).toBe('14:05')
    expect(hhmmss(winter, 'Asia/Tokyo')).toBe('23:05:07')
    expect(hhmm(winter, 'Europe/London')).toBe('14:05')
  })
  it('midnight is 00:xx, never 24:xx', () => {
    expect(hhmm(utc('2025-03-04T05:00:00Z'), 'America/New_York')).toBe('00:00')
  })
  it('full time and tick marks', () => {
    const t = utc('2025-03-04T14:05:00Z')
    expect(fullTime(t, 'UTC')).toBe('Tue 04 Mar 14:05')
    expect(tickMark(t, 3, 'America/New_York')).toBe('09:05')
    expect(tickMark(t, 4, 'UTC')).toBe('14:05:00')
    expect(tickMark(t, 2, 'UTC')).toBe('04 Mar')
    expect(tickMark(t, 0, 'UTC')).toBe('2025')
  })
})

describe('delay parsing', () => {
  it('accepts seconds, minutes and hours', () => {
    expect(parseDelay('90')).toBe(90)
    expect(parseDelay('90s')).toBe(90)
    expect(parseDelay('5m')).toBe(300)
    expect(parseDelay(' 1.5m ')).toBe(90)
    expect(parseDelay('2h')).toBe(7200)
  })
  it('rejects nonsense and out-of-range values', () => {
    for (const bad of ['', 'abc', '0', '-5', '5x', '1e3', '25h', '0.2']) expect(parseDelay(bad)).toBeNull()
  })
  it('formats back', () => {
    expect(formatDelay(60)).toBe('1m')
    expect(formatDelay(300)).toBe('5m')
    expect(formatDelay(900)).toBe('15m')
    expect(formatDelay(90)).toBe('90s')
    expect(formatDelay(3600)).toBe('1h')
  })
})
