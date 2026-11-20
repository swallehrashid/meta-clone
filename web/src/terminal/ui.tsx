import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react'
import { IArrowDown, IArrowUp, IBack, ICheck, IMenu } from './icons'

/** App bar: hamburger (or back arrow) + title (+ optional subtitle) + action icons. */
export function Header(p: { left?: 'menu' | 'back' | 'none'; onLeft?: () => void; title?: ReactNode; sub?: ReactNode; children?: ReactNode; testId?: string }) {
  return (
    <header className="hdr" data-testid={p.testId}>
      {p.left !== 'none' && (
        <button className="ib lead" onClick={p.onLeft} aria-label={p.left === 'back' ? 'Back' : 'Menu'} data-testid={p.left === 'back' ? 'back' : 'menu'}>
          {p.left === 'back' ? <IBack /> : <IMenu />}
        </button>
      )}
      {p.sub !== undefined ? <div className="ttl two"><span>{p.title}</span><small>{p.sub}</small></div> : <div className="ttl">{p.title}</div>}
      <div className="acts">{p.children}</div>
    </header>
  )
}

export function IconBtn(p: { onClick: () => void; label: string; children: ReactNode; testId?: string; active?: boolean; style?: CSSProperties }) {
  return <button className={`ib${p.active ? ' on' : ''}`} onClick={p.onClick} aria-label={p.label} title={p.label} data-testid={p.testId} style={p.style}>{p.children}</button>
}

export interface MenuItem {
  label: ReactNode
  sub?: ReactNode
  icon?: ReactNode
  checked?: boolean
  sort?: 'up' | 'down' | null
  disabled?: boolean
  onClick: () => void
  testId?: string
}

/** The rounded dark popup menu MT5 opens from a toolbar icon (sort, period, chips) or a long press. */
export function Menu(p: { items: MenuItem[]; onClose: () => void; at?: { top?: number; right?: number; left?: number; bottom?: number }; wide?: boolean; testId?: string }) {
  useEscape(p.onClose)
  return (
    <>
      <div className="veil clear" onClick={p.onClose} />
      <div className={`menu${p.wide ? ' wide' : ''}`} style={{ top: p.at?.top ?? 64, right: p.at?.right, left: p.at?.left, bottom: p.at?.bottom }} role="menu" data-testid={p.testId ?? 'menu'}>
        {p.items.map((it, i) => (
          <button key={i} className={`mi${it.disabled ? ' dis' : ''}`} role="menuitem" data-testid={it.testId}
                  onClick={() => { if (it.disabled) return; p.onClose(); it.onClick() }}>
            {it.icon && <span className="mic">{it.icon}</span>}
            <span className="mtx"><span>{it.label}</span>{it.sub && <small>{it.sub}</small>}</span>
            {it.checked && <span className="mck"><ICheck size={22} /></span>}
            {it.sort && <span className="mck">{it.sort === 'up' ? <IArrowUp size={22} /> : <IArrowDown size={22} />}</span>}
          </button>
        ))}
      </div>
    </>
  )
}

/** A centred dialog (confirmations, PIN, disclaimers). */
export function Dialog(p: { title?: ReactNode; children?: ReactNode; actions: { label: string; onClick: () => void; primary?: boolean; testId?: string }[]; onClose: () => void; testId?: string }) {
  useEscape(p.onClose)
  return (
    <>
      <div className="veil" onClick={p.onClose} />
      <div className="dlg" role="dialog" data-testid={p.testId ?? 'dialog'}>
        {p.title && <h3>{p.title}</h3>}
        <div className="dbody">{p.children}</div>
        <div className="dacts">{p.actions.map((a) => <button key={a.label} className={a.primary ? 'pri' : ''} onClick={a.onClick} data-testid={a.testId}>{a.label}</button>)}</div>
      </div>
    </>
  )
}

/** MT5's square blue checkbox. */
export function Check({ on }: { on: boolean }) {
  return <span className={`chk${on ? ' on' : ''}`} aria-hidden>{on && <ICheck size={20} color="#fff" />}</span>
}

export function Empty({ icon, text, children }: { icon: ReactNode; text: string; children?: ReactNode }) {
  return <div className="empty"><div className="eic">{icon}</div><div className="etx">{text}</div>{children}</div>
}

/** A pushed page: back arrow, title, actions, scrolling body. */
export function Page(p: { title: ReactNode; sub?: ReactNode; onBack: () => void; actions?: ReactNode; children: ReactNode; testId?: string; className?: string }) {
  return (
    <div className={`page ${p.className ?? ''}`} data-testid={p.testId}>
      <Header left="back" onLeft={p.onBack} title={p.title} sub={p.sub}>{p.actions}</Header>
      <div className="scroll">{p.children}</div>
    </div>
  )
}

export function SettingRow(p: { title: ReactNode; sub?: ReactNode; right?: ReactNode; onClick?: () => void; testId?: string; on?: boolean }) {
  return (
    <button className={`srow${p.on ? ' on' : ''}`} onClick={p.onClick} data-testid={p.testId}>
      <span className="stx"><span className="st">{p.title}</span>{p.sub && <span className="ss">{p.sub}</span>}</span>
      {p.right !== undefined && <span className="sr">{p.right}</span>}
    </button>
  )
}

export function useEscape(f: () => void) {
  const r = useRef(f)
  r.current = f
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') r.current() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])
}

/** Tap vs long press (500 ms), the way MT5 rows behave. Returns pointer handlers for an element. */
export function longPress(onTap: () => void, onLong: (pt: { x: number; y: number }) => void, ms = 480) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let fired = false
  let moved = false
  let start = { x: 0, y: 0 }
  const clear = () => { if (timer) { clearTimeout(timer); timer = null } }
  return {
    onPointerDown: (e: React.PointerEvent) => {
      fired = false; moved = false; start = { x: e.clientX, y: e.clientY }
      clear(); timer = setTimeout(() => { fired = true; onLong(start) }, ms)
    },
    onPointerMove: (e: React.PointerEvent) => { if (Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) > 12) { moved = true; clear() } },
    onPointerUp: () => { clear(); if (!fired && !moved) onTap() },
    onPointerLeave: clear,
    onPointerCancel: clear,
    onContextMenu: (e: React.MouseEvent) => { e.preventDefault(); clear(); fired = true; onLong({ x: e.clientX, y: e.clientY }) },
  }
}
