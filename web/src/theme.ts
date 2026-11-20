// The ONE place the look of the app is defined. Change colours here (chart + UI both read it).
export const theme = {
  // MT5-style: black canvas, green/red candles, thin dark grid
  background: '#000000',
  panel: '#0d0d0d',
  panelBorder: '#262626',
  text: '#c4c4c4',
  textDim: '#7d7d7d',
  grid: '#1a1a1a',
  crosshair: '#6b6b6b',
  scaleBorder: '#2b2b2b',

  candleUp: '#2ebd6b',
  candleDown: '#e5534b',

  bidLine: '#4da3ff',     // MT5 draws the bid line in blue
  askLine: '#ff6b6b',     // and the ask line in red (toggle)

  labelWin: '#2ecc71',
  labelLoss: '#ff4d4d',
  labelNeutral: '#8a8a8a',
  callBuy: '#4da3ff',
  callSell: '#ffa94d',

  accent: '#4da3ff',
  buyButton: '#1f6feb',
  sellButton: '#d63a3a',
  peek: '#ff9f0a',
  danger: '#ff453a',
} as const

export type Theme = typeof theme

/** Expose the theme to CSS as custom properties (--bg, --text, ...). */
export function applyCssVars(root: HTMLElement = document.documentElement): void {
  const map: Record<string, string> = {
    '--bg': theme.background, '--panel': theme.panel, '--panel-border': theme.panelBorder,
    '--text': theme.text, '--dim': theme.textDim, '--accent': theme.accent,
    '--buy': theme.buyButton, '--sell': theme.sellButton, '--peek': theme.peek,
    '--danger': theme.danger, '--up': theme.candleUp, '--down': theme.candleDown,
  }
  for (const [k, v] of Object.entries(map)) root.style.setProperty(k, v)
}
