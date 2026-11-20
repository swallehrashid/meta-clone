import type { Label } from './types'

type Fetcher = (q: { symbol: string; tFrom: number; delay: number }) => Promise<{ labels: Label[] }>

/**
 * Keeps the labels for one symbol without re-downloading history on every refresh:
 * the first load (or a wider range / new delay) fetches everything from `tFrom`; later
 * refreshes only ask for the last few minutes, which is where pending labels finalise.
 */
export class LabelsCache {
  private map = new Map<string, Label>()
  private minFrom = Infinity
  private delay = -1
  private gen = 0   // bumped on every full reload; late answers from an older generation are dropped
  /** how far back a refresh re-reads (covers the 5 min outcome window + slack) */
  static readonly REFRESH_BACK_MS = 8 * 60_000

  constructor(public readonly symbol: string, private fetcher: Fetcher) {}

  get labels(): Label[] { return [...this.map.values()] }

  async refresh(tFrom: number, delay: number): Promise<boolean> {
    const full = delay !== this.delay || tFrom < this.minFrom
    if (full) { this.map.clear(); this.gen++ }
    const gen = this.gen
    let from = tFrom
    if (!full) {
      let newest = 0
      for (const l of this.map.values()) if (l.entry_t_ms > newest) newest = l.entry_t_ms
      from = Math.max(tFrom, newest - LabelsCache.REFRESH_BACK_MS)
    }
    const { labels } = await this.fetcher({ symbol: this.symbol, tFrom: from, delay })
    if (gen !== this.gen) return false      // superseded while in flight (e.g. delay changed)
    for (const l of labels) this.map.set(`${l.entry_t_ms}:${l.direction}`, l)
    if (full) { this.minFrom = tFrom; this.delay = delay }
    return labels.length > 0 || full
  }
}
