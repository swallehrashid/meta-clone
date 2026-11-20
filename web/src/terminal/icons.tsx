import type { ReactNode } from 'react'

// Inline SVG icons drawn to match the MetaTrader 5 Android app (outline style, 24 px grid). currentColor everywhere.
type P = { size?: number; color?: string; className?: string }
const svg = (children: ReactNode, { size = 24, color = 'currentColor', className }: P, sw = 2) => (
  <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={sw}
       strokeLinecap="round" strokeLinejoin="round" aria-hidden>{children}</svg>
)
const fill = (children: ReactNode, { size = 24, color = 'currentColor', className }: P) => (
  <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill={color} aria-hidden>{children}</svg>
)

// ---- app bar
export const IMenu = (p: P) => svg(<path d="M3 6h18M3 12h18M3 18h18" />, p)
export const IBack = (p: P) => svg(<><path d="M20 12H4" /><path d="M10 6l-6 6 6 6" /></>, p)
export const IPlus = (p: P) => svg(<path d="M12 4v16M4 12h16" />, p)
export const IPencil = (p: P) => svg(<><path d="M4 20l1-4.5L15.5 5a2.1 2.1 0 013 3L8 18.5z" /><path d="M13.5 7l3 3" /></>, p, 1.8)
export const ISearch = (p: P) => svg(<><circle cx="10.5" cy="10.5" r="6.5" /><path d="M15.5 15.5L21 21" /></>, p)
export const IMore = (p: P) => fill(<><circle cx="12" cy="5" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="12" cy="19" r="2" /></>, p)
export const IDots = (p: P) => fill(<><circle cx="4" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="20" cy="12" r="2" /></>, p)
export const ITrash = (p: P) => fill(<path d="M9 3h6l1 1h4v2H4V4h4l1-1zM6 8h12l-1 12a2 2 0 01-2 2H9a2 2 0 01-2-2L6 8z" />, p)
export const IEyeOff = (p: P) => svg(<><path d="M2 12s3.5-7 10-7c2 0 3.7.6 5.1 1.5M22 12s-3.5 7-10 7c-2 0-3.7-.6-5.1-1.5" /><circle cx="12" cy="12" r="3" /><path d="M3 3l18 18" /></>, p, 1.8)
export const IEye = (p: P) => svg(<><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></>, p, 1.8)
export const IClose = (p: P) => svg(<path d="M6 6l12 12M18 6L6 18" />, p)
export const ICheck = (p: P) => svg(<path d="M4 12.5l5 5L20 6.5" />, p, 2.4)
export const IHome = (p: P) => svg(<><path d="M3 11l9-7 9 7" /><path d="M5 10v10h14V10" /></>, p, 1.8)
export const IFolder = (p: P) => fill(<path d="M3 6a2 2 0 012-2h4l2 2h8a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2V6z" />, { color: '#f5b72f', ...p })
export const IDrag = (p: P) => svg(<path d="M4 7h16M4 12h16M4 17h16" />, p, 1.8)
export const IArrowUp = (p: P) => svg(<><path d="M12 20V5" /><path d="M6 11l6-6 6 6" /></>, p, 2.2)
export const IArrowDown = (p: P) => svg(<><path d="M12 4v15" /><path d="M6 13l6 6 6-6" /></>, p, 2.2)
export const IArrowRight = (p: P) => svg(<><path d="M4 12h15" /><path d="M13 6l6 6-6 6" /></>, p, 2.2)
export const IChevDown = (p: P) => svg(<path d="M6 9l6 6 6-6" />, p, 2.2)
export const IChevUp = (p: P) => svg(<path d="M6 15l6-6 6 6" />, p, 2.2)
export const IChevRight = (p: P) => svg(<path d="M9 6l6 6-6 6" />, p, 2)
export const IUpDown = (p: P) => svg(<><path d="M7 10l5-5 5 5" /><path d="M7 14l5 5 5-5" /></>, p, 2.2)
export const ISort = (p: P) => svg(<><path d="M7 20V5M3 9l4-4 4 4" /><path d="M17 4v15M13 15l4 4 4-4" /></>, p, 1.9)
export const IDocPlus = (p: P) => svg(<><path d="M6 3h8l5 5v13H6z" /><path d="M14 3v5h5" /><path d="M12.5 11v7M9 14.5h7" /></>, p, 1.8)
export const IGear = (p: P) => svg(<><circle cx="12" cy="12" r="3.2" /><path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" /></>, p, 1.7)
export const IPrint = (p: P) => svg(<><path d="M7 8V3h10v5" /><rect x="3" y="8" width="18" height="9" rx="2" /><path d="M7 14h10v7H7z" /></>, p, 1.8)
export const IDownload = (p: P) => svg(<><path d="M12 4v11M7 10l5 5 5-5" /><path d="M4 19h16" /></>, p, 1.9)
export const IClock = (p: P) => svg(<><circle cx="12" cy="12" r="8" /><path d="M12 8v4l2.5 2" /></>, p, 1.6)
export const ISpread = (p: P) => svg(<path d="M5 5v14M19 5v14M5 12h14M5 7h14M5 17h14" />, p, 1.5)
export const IMinus = (p: P) => svg(<path d="M5 12h14" />, p)
export const IFx = (p: P) => svg(<><path d="M10 5c-2 0-3 1-3 3v11M4.5 10H10" /><circle cx="17" cy="16" r="4.2" /><path d="M17 14v4M15 16h4" /></>, p, 1.8)
export const ILock = (p: P) => svg(<><rect x="5" y="11" width="14" height="10" rx="2" /><path d="M8 11V7a4 4 0 018 0v4" /></>, p, 1.8)
export const IBackspace = (p: P) => svg(<><path d="M21 5H9l-6 7 6 7h12z" /><path d="M12 9l6 6M18 9l-6 6" /></>, p, 1.8)

// ---- chart toolbar
export const ICrosshair = (p: P) => svg(<path d="M12 3v7M12 14v7M3 12h7M14 12h7" />, p, 1.8)
export const IMove = (p: P) => svg(<><path d="M12 3v18M3 12h18" /><path d="M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3M18 9l3 3-3 3" /></>, p, 1.8)
export const IIndicators = (p: P) => svg(<><path d="M3 18c2-1 3-8 6-8s3 6 6 6 3-6 6-9" /><path d="M14 5h6v6" /><path d="M8 4.5c1 0 1.5.6 1.5 1.5S9 8 8 8" /></>, p, 1.8)
/** The red/blue ring with a clock hand: opens the order panel */
export const IOrderRing = ({ size = 30 }: P) => (
  <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden>
    <path d="M15 4 A11 11 0 0 0 15 26" fill="none" stroke="#e8453c" strokeWidth="5" />
    <path d="M15 4 A11 11 0 0 1 15 26" fill="none" stroke="#2f7ff0" strokeWidth="5" />
    <circle cx="15" cy="15" r="6.5" fill="#fff" />
    <path d="M15 11v4.3l2.6 1.5" stroke="#333" strokeWidth="1.6" fill="none" strokeLinecap="round" />
  </svg>
)
/** The red/blue panel icon: shows / hides the one-click trading panel */
export const IOneClick = ({ size = 30 }: P) => (
  <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden>
    <rect x="2" y="6" width="26" height="18" rx="4" fill="#e8453c" />
    <path d="M15 6h9a4 4 0 014 4v10a4 4 0 01-4 4h-9z" fill="#2f7ff0" />
    <rect x="8" y="12" width="14" height="6" rx="3" fill="#fff" />
  </svg>
)

// ---- bottom navigation
export const NQuotes = (p: P) => svg(<><path d="M8 20V5M4 9l4-4 4 4" /><path d="M16 4v15M12 15l4 4 4-4" /></>, p, 2.1)
export const NCharts = (p: P) => svg(<><rect x="4" y="7" width="5" height="10" rx="1" /><path d="M6.5 3v4M6.5 17v4" /><rect x="15" y="5" width="5" height="11" rx="1" /><path d="M17.5 2v3M17.5 16v6" /></>, p, 2)
export const NTrade = (p: P) => svg(<><path d="M4 18l5-6 4 3 7-9" /><circle cx="4" cy="18" r="2" fill="currentColor" /><circle cx="9" cy="12" r="2" fill="currentColor" /><circle cx="13" cy="15" r="2" fill="currentColor" /><circle cx="20" cy="6" r="2" fill="currentColor" /></>, p, 2)
export const NHistory = (p: P) => svg(<><path d="M3.5 12a8.5 8.5 0 108.5-8.5A8.5 8.5 0 006 6.3" /><path d="M3 3.5v4.5h4.5" /><path d="M12 8v4.5l3 2" /></>, p, 2)
export const NMessages = (p: P) => svg(<><path d="M3 5a2 2 0 012-2h11a2 2 0 012 2v8a2 2 0 01-2 2H9l-4 3.5V15H5a2 2 0 01-2-2z" /><path d="M21 8v8a2 2 0 01-2 2h-1v3l-3.5-3H11" /></>, p, 1.9)

// ---- history header
export const IExchange = (p: P) => svg(<><path d="M20 12a8 8 0 01-13.7 5.6M4 12a8 8 0 0113.7-5.6" /><path d="M18 3v4h-4M6 21v-4h4" /><path d="M14.2 9.6c-.4-.8-1.2-1.2-2.2-1.2-1.3 0-2.2.7-2.2 1.6 0 2.4 4.6 1.1 4.6 3.6 0 1-1 1.7-2.4 1.7-1.1 0-2-.5-2.4-1.3M12 7.2v1.2M12 15.3v1.2" /></>, p, 1.7)
export const ICalendar = (p: P) => svg(<><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M8 3v4M16 3v4M3 10h18" /><path d="M7.5 14h.01M11 14h.01M14.5 14h.01M7.5 17.5h.01M11 17.5h.01M14.5 17.5h.01" strokeWidth="2.4" /></>, p, 1.8)
export const ICalendarSolid = ({ size = 28, n }: P & { n?: string }) => (
  <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden>
    <rect x="3" y="5" width="22" height="20" rx="3" fill="#9a9a9a" /><rect x="8" y="2" width="3" height="6" rx="1.5" fill="#9a9a9a" /><rect x="17" y="2" width="3" height="6" rx="1.5" fill="#9a9a9a" />
    <rect x="3" y="9" width="22" height="2" fill="#5a5a5a" />
    {n ? <text x="14" y="22" textAnchor="middle" fontSize="11" fontWeight="700" fill="#2b2b2b" fontFamily="Roboto, Arial, sans-serif">{n}</text>
       : <g fill="#2b2b2b"><circle cx="9" cy="15" r="1.4" /><circle cx="14" cy="15" r="1.4" /><circle cx="19" cy="15" r="1.4" /><circle cx="9" cy="20" r="1.4" /><circle cx="14" cy="20" r="1.4" /><circle cx="19" cy="20" r="1.4" /></g>}
  </svg>
)
export const IReport = (p: P) => svg(<><path d="M3 20h18" /><path d="M5 17V9M9 17V6M13 17v-5M17 17V9" /><path d="M4 7l5-3 4 5 7-5" /></>, p, 1.9)

// ---- drawer
export const DTrade = NTrade
export const DNews = (p: P) => svg(<><path d="M5 4h14v16H7a2 2 0 01-2-2z" /><path d="M5 9H3v9a2 2 0 002 2" /><path d="M9 8h6M9 12h6M9 16h4" /></>, p, 1.8)
export const DMail = (p: P) => svg(<><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>, p, 1.8)
export const DJournal = (p: P) => svg(<><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M12 4v16M6 9h3M6 13h3M15 9h3M15 13h3" /></>, p, 1.8)
export const DSettings = IGear
export const DCalendar = (p: P) => svg(<><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M8 3v4M16 3v4M3 10h18" /></>, p, 1.8)
export const DCommunity = (p: P) => svg(<><circle cx="8" cy="7" r="2.5" /><circle cx="16" cy="7" r="2.5" /><path d="M3 21v-5a3 3 0 013-3h4a3 3 0 013 3v5M13.5 13H18a3 3 0 013 3v5" /></>, p, 1.8)
export const DAlgo = (p: P) => svg(<path d="M3 11l18-8-8 18-2-8z" />, p, 1.8)
export const DHelp = (p: P) => svg(<><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 015 .5c0 2-2.5 2-2.5 4M12 17h.01" /></>, p, 1.8)
export const DInfo = (p: P) => svg(<><circle cx="12" cy="12" r="9" /><path d="M12 11v6M12 7.5h.01" /></>, p, 1.9)

// ---- empty states
export const EInbox = ({ size = 96 }: P) => (
  <svg width={size} height={size} viewBox="0 0 96 96" aria-hidden><path d="M18 30l8-16h44l8 16v40a6 6 0 01-6 6H24a6 6 0 01-6-6z" fill="#3c3c3e" />
    <path d="M18 34h20a10 10 0 0020 0h20" fill="none" stroke="#1c1c1e" strokeWidth="4" /></svg>
)
export const EChat = ({ size = 96 }: P) => (
  <svg width={size} height={size} viewBox="0 0 96 96" aria-hidden><path d="M48 12c22 0 38 13 38 30S70 72 48 72c-4 0-8-.5-11-1.4L18 82l4-17C14 59 10 51 10 42c0-17 16-30 38-30z" fill="#3c3c3e" />
    <circle cx="32" cy="42" r="5" fill="#000" /><circle cx="48" cy="42" r="5" fill="#000" /><circle cx="64" cy="42" r="5" fill="#000" /></svg>
)
export const EQuotes = ({ size = 72 }: P) => svg(<><path d="M8 20V5M4 9l4-4 4 4" /><path d="M16 4v15M12 15l4 4 4-4" /></>, { size, color: '#3c3c3e' }, 2.4)
