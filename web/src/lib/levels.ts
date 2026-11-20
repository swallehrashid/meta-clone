/** Hit-testing and drag maths for the SL / TP / pending-price lines drawn on a chart (pure, testable). */
export interface Level {
  key: string                       // e.g. "p12:sl"
  id: number
  kind: 'position' | 'order'
  field: 'sl' | 'tp' | 'price'
  price: number
}

/** The level whose line is within `tol` px of y (closest wins); `toY` maps a price to a pixel row. */
export function hitLevel(levels: Level[], y: number, toY: (price: number) => number | null, tol = 14): Level | null {
  let best: Level | null = null
  let bestD = tol + 1e-9
  for (const l of levels) {
    if (l.field === 'price' && l.kind === 'position') continue
    const ly = toY(l.price)
    if (ly === null) continue
    const d = Math.abs(ly - y)
    if (d <= bestD) { best = l; bestD = d }
  }
  return best
}

/** Price under the pointer, snapped to the instrument's digits. */
export function dragPrice(y: number, toPrice: (y: number) => number | null, digits: number): number | null {
  const p = toPrice(y)
  return p === null || !isFinite(p) ? null : Number(p.toFixed(digits))
}

export function levelsOf(positions: { id: number; sl: number | null; tp: number | null }[],
                         orders: { id: number; price: number | null; sl: number | null; tp: number | null }[]): Level[] {
  const out: Level[] = []
  for (const p of positions) {
    if (p.sl !== null) out.push({ key: `p${p.id}:sl`, id: p.id, kind: 'position', field: 'sl', price: p.sl })
    if (p.tp !== null) out.push({ key: `p${p.id}:tp`, id: p.id, kind: 'position', field: 'tp', price: p.tp })
  }
  for (const o of orders) {
    if (o.price !== null) out.push({ key: `o${o.id}:price`, id: o.id, kind: 'order', field: 'price', price: o.price })
    if (o.sl !== null) out.push({ key: `o${o.id}:sl`, id: o.id, kind: 'order', field: 'sl', price: o.sl })
    if (o.tp !== null) out.push({ key: `o${o.id}:tp`, id: o.id, kind: 'order', field: 'tp', price: o.tp })
  }
  return out
}
