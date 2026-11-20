import { createContext, useContext } from 'react'
import type { Quote, TerminalSettings } from '../lib/api'
import type { MarketStore } from '../lib/store'
import type { Deal, OrderType, TOrder, TradeState } from '../lib/trade'
import type { Prefs, TfName } from './prefs'

export type Tab = 'quotes' | 'chart' | 'trade' | 'history' | 'messages'
  | 'settings' | 'news' | 'mailbox' | 'journal' | 'calendar' | 'community' | 'algo' | 'guide' | 'about'

export type PageSpec =
  | { id: 'addSymbol' } | { id: 'folder'; folder: string } | { id: 'selectedSymbols' }
  | { id: 'properties'; symbol: string } | { id: 'statistics'; symbol: string }
  | { id: 'indicators' } | { id: 'indicatorList'; window: number } | { id: 'indicatorParams'; type: string; window: number; editId?: string }
  | { id: 'chartSettings' } | { id: 'report' } | { id: 'mail'; idx: number } | { id: 'accounts' } | { id: 'otp' }

export interface QuoteView extends Quote { bidUp: boolean; askUp: boolean; stale: boolean }

export interface NewOrder {
  symbol: string; side: 'buy' | 'sell'; type: OrderType; volume: number
  price?: number | null; stoplimit?: number | null; sl?: number | null; tp?: number | null
  expiration?: 'gtc' | 'today' | 'specified'; expire_ms?: number | null; comment?: string
}

/** What the order panel is doing: a new order, closing / modifying a position, or modifying a pending order. */
export type PanelMode =
  | { kind: 'new'; symbol: string; type?: string; side?: 'buy' | 'sell'; price?: number | null; expanded?: boolean }
  | { kind: 'close'; positionId: number } | { kind: 'modify'; positionId: number } | { kind: 'order'; orderId: number }

export interface T {
  cfg: TerminalSettings
  prefs: Prefs
  setPrefs: (p: Partial<Prefs>) => void
  store: MarketStore
  allSymbols: string[]                   // broker names the server trades
  symbols: string[]                      // the Quotes window: user's selection and order
  describe: (s: string) => string
  digitsOf: (s: string) => number
  quotes: Map<string, QuoteView>
  trade: TradeState | null
  deals: Deal[]
  ordersHistory: TOrder[]
  refreshTrade: () => void
  refreshHistory: () => void
  // navigation
  tab: Tab
  setTab: (t: Tab) => void
  push: (p: PageSpec) => void
  pop: () => void
  openDrawer: () => void
  // chart
  chartSym: string
  setChartSym: (s: string) => void
  tf: TfName
  setTf: (t: TfName) => void
  panel: PanelMode | null
  setPanel: (m: PanelMode | null) => void
  // feedback + actions
  toast: (text: string, bad?: boolean) => void
  place: (o: NewOrder) => Promise<boolean>
  closePos: (id: number, volume?: number) => Promise<boolean>
  modifyPos: (id: number, sl: number | null, tp: number | null) => Promise<boolean>
  modifyOrd: (id: number, change: { price?: number; sl?: number | null; tp?: number | null; stoplimit?: number | null }) => Promise<boolean>
  cancelOrd: (id: number) => Promise<boolean>
}

export const Ctx = createContext<T | null>(null)
export function useT(): T {
  const t = useContext(Ctx)
  if (!t) throw new Error('terminal context missing')
  return t
}
