// Formatting that matches what MetaTrader 5 on a phone prints. Times are shown in the (admin-chosen) server time zone.

/** "1.22456" -> pre "1.22", big "45", sup "6": MT5 draws the middle two digits large and the last one as a superscript. */
export function priceParts(v: number, digits: number): { pre: string; big: string; sup: string } {
  const s = v.toFixed(digits)
  if (s.length < 4) return { pre: '', big: s, sup: '' }
  return { pre: s.slice(0, -3), big: s.slice(-3, -1), sup: s.slice(-1) }
}

const cache = new Map<string, Intl.DateTimeFormat>()
function fmt(tz: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = tz + JSON.stringify(opts)
  let f = cache.get(key)
  if (!f) { f = new Intl.DateTimeFormat('en-GB', { ...opts, timeZone: tz }); cache.set(key, f) }
  return f
}
function parts(ms: number, tz: string): Record<string, string> {
  const o: Record<string, string> = {}
  for (const p of fmt(tz, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(ms)) o[p.type] = p.value
  return o
}

/** "17:40:44" */
export function hms(ms: number, tz: string): string { const p = parts(ms, tz); return `${p.hour}:${p.minute}:${p.second}` }
/** "2026.11.10 16:13:54" (the History screen) */
export function stamp(ms: number, tz: string): string { const p = parts(ms, tz); return `${p.year}.${p.month}.${p.day} ${p.hour}:${p.minute}:${p.second}` }
/** "10 Nov 18:00" (the chart's time axis) */
export function dayMonHm(sec: number, tz: string): string {
  const d = fmt(tz, { day: 'numeric', month: 'short' }).format(sec * 1000)
  const p = parts(sec * 1000, tz)
  return `${d} ${p.hour}:${p.minute}`
}
export function dayMon(sec: number, tz: string): string { return fmt(tz, { day: 'numeric', month: 'short' }).format(sec * 1000) }

/** 9884.69 -> "9 884.69" (a thin space between thousands, as MT5 prints) */
export function money(v: number | null | undefined): string {
  if (v === null || v === undefined || !isFinite(v)) return '–'
  const s = Math.abs(v).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return (v < 0 && Math.abs(v) >= 0.005 ? '-' : '') + s
}

/** Time left in the forming candle: "19:14" (or "1:05:30" beyond an hour) */
export function countdown(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60
  const mm = String(m).padStart(2, '0'), ss = String(r).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export const signed = (v: number, digits = 0): string => (v > 0 ? '+' : v < 0 ? '-' : '') + Math.abs(v).toFixed(digits)

/** "Tue 17 Nov '26 05:25" (the crosshair's time label) */
export function crossTime(sec: number, tz: string): string {
  const wd = fmt(tz, { weekday: 'short' }).format(sec * 1000)
  const p = parts(sec * 1000, tz)
  const mon = fmt(tz, { month: 'short' }).format(sec * 1000)
  return `${wd} ${Number(p.day)} ${mon} '${p.year.slice(2)} ${p.hour}:${p.minute}`
}
/** "17/11/2026" (History period menu) */
export function dmy(ms: number, tz: string): string { const p = parts(ms, tz); return `${p.day}/${p.month}/${p.year}` }
/** "2026.11.17 08:47" */
export function stampMin(ms: number, tz: string): string { const p = parts(ms, tz); return `${p.year}.${p.month}.${p.day} ${p.hour}:${p.minute}` }
/** start of the local day (in tz) that contains ms */
export function dayStart(ms: number, tz: string): number {
  const p = parts(ms, tz)
  const asUtc = Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour === 24 ? 0 : +p.hour, +p.minute, +p.second)
  const offset = asUtc - Math.floor(ms / 1000) * 1000
  return Date.UTC(+p.year, +p.month - 1, +p.day) - offset
}
export function tzParts(ms: number, tz: string): { year: number; month: number; day: number; hour: number; minute: number; weekday: number } {
  const p = parts(ms, tz)
  const wd = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(fmt(tz, { weekday: 'short' }).format(ms))
  return { year: +p.year, month: +p.month, day: +p.day, hour: +p.hour % 24, minute: +p.minute, weekday: wd }
}
/** "1d 2h", "35m", "12s" */
export function duration(sec: number): string {
  const s = Math.max(0, Math.round(sec))
  if (s >= 86400) return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`
  if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
  if (s >= 60) return `${Math.floor(s / 60)}m`
  return `${s}s`
}
/** volume like MT5: 0.1, 0.03, 1 (no trailing zeros) */
export const vol = (v: number) => String(Number(v.toFixed(2)))
