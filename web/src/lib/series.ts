import type { Bar } from './types'

/**
 * Merge incoming bars into `bars` IN PLACE (oldest first). A bar with the same time
 * replaces the existing one (the forming candle), a newer time is appended, an older one
 * is spliced in. Returns the bars that were applied, in order, for `series.update()`.
 */
export function mergeBars(bars: Bar[], incoming: Bar[]): Bar[] {
  const applied: Bar[] = []
  for (const b of incoming) {
    const last = bars[bars.length - 1]
    if (!last || b.time > last.time) {
      bars.push({ ...b })
    } else if (b.time === last.time) {
      bars[bars.length - 1] = { ...b }
    } else {
      let i = bars.length - 1
      while (i >= 0 && bars[i].time > b.time) i--
      if (i >= 0 && bars[i].time === b.time) bars[i] = { ...b }
      else bars.splice(i + 1, 0, { ...b })
    }
    applied.push(b)
  }
  return applied
}

/** Keep memory bounded on a long session: drop the oldest bars beyond `max`. */
export function trimBars(bars: Bar[], max: number): void {
  if (bars.length > max) bars.splice(0, bars.length - max)
}
