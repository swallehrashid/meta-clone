/** Chart times are UTC seconds; these render them in the user's chosen display timezone. */

export const TIMEZONES: { id: string; label: string }[] = [
  { id: 'America/New_York', label: 'New York' },
  { id: 'UTC', label: 'UTC' },
  { id: 'Europe/London', label: 'London' },
  { id: 'Europe/Berlin', label: 'Frankfurt' },
  { id: 'Asia/Tokyo', label: 'Tokyo' },
  { id: 'local', label: 'Device' },
]

const cache = new Map<string, Intl.DateTimeFormat>()
function fmt(tz: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(opts)
  let f = cache.get(key)
  if (!f) {
    f = new Intl.DateTimeFormat('en-GB', { ...opts, ...(tz === 'local' ? {} : { timeZone: tz }) })
    cache.set(key, f)
  }
  return f
}

/** "09:05" */
export function hhmm(sec: number, tz: string): string {
  return fmt(tz, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(sec * 1000)
}
/** "09:05:07" */
export function hhmmss(sec: number, tz: string): string {
  return fmt(tz, { hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(sec * 1000)
}
/** "Tue 04 Mar 09:05" (crosshair / tooltip) */
export function fullTime(sec: number, tz: string): string {
  const d = fmt(tz, { weekday: 'short', day: '2-digit', month: 'short' }).format(sec * 1000)
  return `${d.replace(',', '')} ${hhmm(sec, tz)}`
}

/** Matches Lightweight Charts' TickMarkType: 0 Year, 1 Month, 2 DayOfMonth, 3 Time, 4 TimeWithSeconds. */
export function tickMark(sec: number, kind: number, tz: string): string {
  switch (kind) {
    case 0: return fmt(tz, { year: 'numeric' }).format(sec * 1000)
    case 1: return fmt(tz, { month: 'short' }).format(sec * 1000)
    case 2: return fmt(tz, { day: '2-digit', month: 'short' }).format(sec * 1000)
    case 4: return hhmmss(sec, tz)
    default: return hhmm(sec, tz)
  }
}

/** Parse a delay such as "90", "90s", "5m", "1.5m", "1h" into seconds, or null if invalid. */
export function parseDelay(text: string, min = 1, max = 86400): number | null {
  const m = /^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$/i.exec(text)
  if (!m) return null
  const n = parseFloat(m[1]) * ({ '': 1, s: 1, m: 60, h: 3600 } as Record<string, number>)[m[2].toLowerCase()]
  const sec = Math.round(n)
  return sec >= min && sec <= max ? sec : null
}

/** 60 -> "1m", 300 -> "5m", 90 -> "90s", 5400 -> "1.5h" */
export function formatDelay(sec: number): string {
  if (sec % 3600 === 0) return `${sec / 3600}h`
  if (sec % 60 === 0) return `${sec / 60}m`
  if (sec < 60) return `${sec}s`
  return `${sec}s`
}
