// Everything the terminal's own Settings screen (and its menus) remembers, per device, like the MT5 app does.
// The SERVER still owns the delay, the tradable pairs and the account; these are display / behaviour choices only.
import { useCallback, useState } from 'react'

export type ThemeName = 'light' | 'dark' | 'black'
export type ChartType = 'bars' | 'candles' | 'line'
export type TfName = 'M1' | 'M5' | 'M15' | 'M30' | 'H1' | 'H4' | 'D1' | 'W1' | 'MN'
export const ALL_TFS: TfName[] = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN']

export interface IndicatorConf {
  id: string
  type: string
  params: Record<string, number | string>
  color: string
  color2?: string
  window: number            // 0 = main chart, 1.. = indicator window
}

export type PeriodKind = 'today' | 'week' | 'month' | '3months' | 'custom'

export interface Prefs {
  theme: ThemeName
  advanced: boolean
  orderSounds: boolean
  oneClick: boolean
  confirmOnChart: boolean          // MT5 wording: checked = do NOT ask for confirmation when trading from the chart
  accountInMenu: boolean
  vibration: 'always' | 'silent' | 'never'
  ringtone: string
  autoDownload: 'always' | 'wifi' | 'never'
  lockScreen: boolean
  pin: string | null
  news: boolean
  language: string
  tablet: boolean
  alwaysOn: boolean
  mqid: string
  // quotes window
  hidden: string[]
  order: string[]
  // chart
  symbol: string | null
  tf: TfName | null
  chartType: ChartType
  showGrid: boolean
  showAsk: boolean | null          // null = follow the server's default
  showVolumes: boolean
  showOHLC: boolean
  showTradeLevels: boolean
  showSeparators: boolean
  colorScheme: 'default' | 'green' | 'bw'
  oneClickPanel: boolean
  volume: number | null
  indicators: Record<string, IndicatorConf[]>
  // history
  period: PeriodKind
  periodFrom: number | null
  periodTo: number | null
  profitInPoints: boolean
  mailRead: number[]
}

const KEY = 'mt5.prefs.v1'

function randomId(): string {
  const a = new Uint8Array(4)
  try { crypto.getRandomValues(a) } catch { for (let i = 0; i < 4; i++) a[i] = Math.floor(Math.random() * 256) }
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('').toUpperCase()
}

export function defaults(): Prefs {
  return {
    theme: 'black', advanced: true, orderSounds: true, oneClick: false, confirmOnChart: false, accountInMenu: true,
    vibration: 'always', ringtone: 'Default (notification_004)', autoDownload: 'always', lockScreen: false, pin: null,
    news: true, language: 'System language', tablet: false, alwaysOn: false, mqid: randomId(),
    hidden: [], order: [],
    symbol: null, tf: null, chartType: 'candles', showGrid: true, showAsk: null, showVolumes: false, showOHLC: false,
    showTradeLevels: true, showSeparators: false, colorScheme: 'default', oneClickPanel: false, volume: null, indicators: {},
    period: 'today', periodFrom: null, periodTo: null, profitInPoints: false, mailRead: [],
  }
}

export function loadPrefs(): Prefs {
  const d = defaults()
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return { ...d, ...JSON.parse(raw) }
  } catch { /* private mode / corrupt: defaults */ }
  try { localStorage.setItem(KEY, JSON.stringify(d)) } catch { /* ignore */ }
  return d
}

export function savePrefs(p: Prefs): void {
  try { localStorage.setItem(KEY, JSON.stringify(p)) } catch { /* ignore */ }
}

export function usePrefs(): [Prefs, (patch: Partial<Prefs>) => void] {
  const [p, setP] = useState<Prefs>(loadPrefs)
  const set = useCallback((patch: Partial<Prefs>) => setP((o) => { const n = { ...o, ...patch }; savePrefs(n); return n }), [])
  return [p, set]
}

/** PIN for the lock screen: never stored in clear text. */
export async function hashPin(pin: string): Promise<string> {
  try {
    const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode('mt5-lock:' + pin))
    return Array.from(new Uint8Array(d), (b) => b.toString(16).padStart(2, '0')).join('')
  } catch {
    let h = 0
    for (const c of 'mt5-lock:' + pin) h = (h * 31 + c.charCodeAt(0)) | 0
    return 'x' + (h >>> 0).toString(16)
  }
}
