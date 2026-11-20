import type { MarketStore } from './store'
import type { ServerMsg, Timeframe } from './types'

export interface StreamParams { symbols: string[]; tf: Timeframe; delay?: number }

/**
 * One websocket to /ws/stream (delayed) or /ws/peek (live). Settings changes are sent as
 * control messages on the open socket - no reconnect, no page reload. On a dropped
 * connection (or a silent one, e.g. phone woke from sleep) it reconnects with backoff and
 * the server's fresh backfill re-syncs the charts.
 */
export class LabStream {
  private ws: WebSocket | null = null
  private closed = false
  private retry = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private watchdog: ReturnType<typeof setInterval> | null = null
  private lastMsg = 0
  /** what the currently open socket was created with (to detect changes made while connecting) */
  private urlParams: StreamParams
  private want: StreamParams

  constructor(private kind: 'stream' | 'peek', params: StreamParams, private store: MarketStore,
              private extraQuery = '', private silenceMs = 8000) {
    this.want = { ...params, symbols: [...params.symbols] }
    this.urlParams = this.want
    this.connect()
    this.watchdog = setInterval(() => this.checkAlive(), 2000)
  }

  private url(): string {
    const p = this.want
    this.urlParams = { ...p, symbols: [...p.symbols] }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const q = new URLSearchParams({ symbols: p.symbols.join(','), tf: p.tf })
    if (this.kind === 'stream' && p.delay !== undefined) q.set('delay', String(p.delay))
    return `${proto}://${location.host}/ws/${this.kind}?${q.toString()}${this.extraQuery}`
  }

  private connect(): void {
    if (this.closed) return
    this.store.setStatus('connecting')
    const ws = new WebSocket(this.url())
    this.ws = ws
    ws.onopen = () => {
      this.retry = 0
      this.lastMsg = Date.now()
      this.store.setStatus('open')
      this.syncDiff()
    }
    ws.onmessage = (ev) => {
      this.lastMsg = Date.now()
      try { this.store.handle(JSON.parse(ev.data) as ServerMsg) } catch (e) { console.error('bad message', e) }
    }
    ws.onclose = (ev) => {
      if (this.ws !== ws) return
      this.ws = null
      if (this.closed) return
      // 4400/4403 = the server refused our parameters: retrying the same thing is pointless
      if (ev.code === 4400 || ev.code === 4403) {
        this.store.setStatus('closed', ev.reason || `refused (${ev.code})`)
        return
      }
      this.store.setStatus('closed')
      const wait = Math.min(1000 * 2 ** this.retry++, 10000)
      this.reconnectTimer = setTimeout(() => this.connect(), wait)
    }
    ws.onerror = () => { /* onclose follows */ }
  }

  private send(msg: object): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg))
  }

  /** Send whatever differs between the open socket's URL and what is wanted now. */
  private syncDiff(): void {
    const a = this.urlParams, b = this.want
    if (this.kind === 'stream' && b.delay !== undefined && a.delay !== b.delay) this.send({ type: 'set_delay', delay: b.delay })
    if (a.tf !== b.tf) this.send({ type: 'set_tf', tf: b.tf })
    if (a.symbols.join() !== b.symbols.join()) this.send({ type: 'set_symbols', symbols: b.symbols })
    this.urlParams = { ...b, symbols: [...b.symbols] }
  }

  setDelay(delay: number): void {
    if (this.kind !== 'stream' || this.want.delay === delay) return
    this.want = { ...this.want, delay }
    this.syncDiff()
  }
  setTf(tf: Timeframe): void {
    if (this.want.tf === tf) return
    this.want = { ...this.want, tf }
    this.syncDiff()
  }
  setSymbols(symbols: string[]): void {
    if (this.want.symbols.join() === symbols.join()) return
    this.want = { ...this.want, symbols: [...symbols] }
    this.syncDiff()
  }

  private checkAlive(): void {
    if (this.ws?.readyState === WebSocket.OPEN && Date.now() - this.lastMsg > this.silenceMs) {
      this.ws.close()                       // server heartbeats every second; silence means a dead link
    }
  }

  close(): void {
    this.closed = true
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
    if (this.watchdog) clearInterval(this.watchdog)
    this.ws?.close()
    this.ws = null
  }
}
