import { useEffect, useState, useSyncExternalStore } from 'react'
import { useT } from './ctx'
import { dmy } from './format'
import {
  DAlgo, DCalendar, DCommunity, DHelp, DInfo, DJournal, DMail, DNews, DSettings, DTrade, EChat, IBackspace, IClose, IEye, IEyeOff, IFx, IPlus,
  ISearch, ITrash, ILock,
} from './icons'
import { INDICATORS, byType, defaultsFor, label as indLabel } from './indicators'
import { clearJournal, journal, onJournal } from './journal'
import { hashPin, type IndicatorConf, type Prefs } from './prefs'
import { RINGTONES, sounds } from './sound'
import { Check, Dialog, Empty, Header, IconBtn, Menu, Page, SettingRow, type MenuItem } from './ui'

const SWATCHES = ['#f5b800', '#4a8ef7', '#ef4f4c', '#36b24a', '#2fbfbf', '#b05cf0', '#ffffff', '#ef6b3a']

// =================================================================================== indicators
export function IndicatorsPage({ onBack }: { onBack: () => void }) {
  const t = useT()
  const list = t.prefs.indicators[t.chartSym] ?? []
  const [confirm, setConfirm] = useState(false)
  const save = (l: IndicatorConf[]) => t.setPrefs({ indicators: { ...t.prefs.indicators, [t.chartSym]: l } })
  const anyHidden = list.some((c) => c.params.__hidden)
  const windows = [...new Set(list.filter((c) => c.window > 0).map((c) => c.window))].sort((a, b) => a - b)
  const Item = ({ c }: { c: IndicatorConf }) => (
    <button className="li" onClick={() => t.push({ id: 'indicatorParams', type: c.type, window: c.window, editId: c.id })} data-testid={`ind-${c.type}`}>
      <span style={{ width: 4, alignSelf: 'stretch', background: c.color, borderRadius: 2 }} />
      <span className="lt"><b>{indLabel(c)}</b>{c.params.__hidden ? <small>hidden</small> : null}</span>
    </button>
  )
  return (
    <Page title={`Indicators on ${t.chartSym}, ${t.tf}`} onBack={onBack} testId="indicators-page" actions={<>
      <IconBtn onClick={() => { if (!list.length) { t.toast('No indicators on this chart'); return } save(list.map((c) => ({ ...c, params: { ...c.params, __hidden: anyHidden ? 0 : 1 } }))) }}
               label={anyHidden ? 'Show all' : 'Hide all'} testId="ind-eye">{anyHidden ? <IEye /> : <IEyeOff />}</IconBtn>
      <IconBtn onClick={() => (list.length ? setConfirm(true) : t.toast('No indicators on this chart'))} label="Delete all" testId="ind-trash"><ITrash /></IconBtn>
    </>}>
      <div className="list">
        <div className="li" style={{ minHeight: 48 }} onClick={() => t.push({ id: 'indicatorList', window: 0 })} data-testid="ind-main-header"><span className="lt"><b style={{ color: 'var(--blue)', fontWeight: 700 }}>MAIN CHART</b></span>
          <span onClick={(e) => e.stopPropagation()}><IconBtn onClick={() => t.push({ id: 'indicatorList', window: 0 })} label="Add indicator" testId="ind-add-main"><IFx /></IconBtn></span></div>
        {list.filter((c) => c.window === 0).map((c) => <Item key={c.id} c={c} />)}
        {windows.map((w, i) => (
          <div key={w}>
            <div className="li" style={{ minHeight: 48 }} onClick={() => t.push({ id: 'indicatorList', window: w })}><span className="lt"><b style={{ color: 'var(--blue)', fontWeight: 700 }}>INDICATOR WINDOW {i + 1}</b></span>
              <span onClick={(e) => e.stopPropagation()}><IconBtn onClick={() => t.push({ id: 'indicatorList', window: w })} label="Add indicator"><IFx /></IconBtn></span></div>
            {list.filter((c) => c.window === w).map((c) => <Item key={c.id} c={c} />)}
          </div>
        ))}
      </div>
      {confirm && <Dialog title="Delete all indicators?" onClose={() => setConfirm(false)} actions={[{ label: 'Cancel', onClick: () => setConfirm(false) },
        { label: 'Delete', primary: true, testId: 'ind-delete-ok', onClick: () => { save([]); setConfirm(false) } }]}>All indicators on {t.chartSym} will be removed.</Dialog>}
    </Page>
  )
}

export function IndicatorListPage({ window: w, onBack }: { window: number; onBack: () => void }) {
  const t = useT()
  const groups = ['Trend', 'Oscillators', 'Volumes', 'Bill Williams'] as const
  return (
    <Page title="Indicators" onBack={onBack} testId="indicator-list">
      <div className="list">
        {groups.map((g) => (
          <div key={g}>
            <div className="seclbl" style={{ color: 'var(--blue)', fontWeight: 700, paddingTop: 16 }}>{g.toUpperCase()}</div>
            {INDICATORS.filter((d) => d.group === g && (w === 0 || d.sub)).map((d) => (
              <button key={d.type} className="li" onClick={() => t.push({ id: 'indicatorParams', type: d.type, window: d.sub ? (w || -1) : 0 })} data-testid={`pick-${d.type}`}>
                <span className="lt"><b>{d.name}</b><small>{d.sub ? 'separate window' : 'main chart'}</small></span>
              </button>
            ))}
          </div>
        ))}
      </div>
    </Page>
  )
}

export function IndicatorParamsPage({ type, window: w, editId, onBack, onDone }: { type: string; window: number; editId?: string; onBack: () => void; onDone: () => void }) {
  const t = useT()
  const def = byType(type)!
  const list = t.prefs.indicators[t.chartSym] ?? []
  const existing = editId ? list.find((c) => c.id === editId) : undefined
  const [params, setParams] = useState<Record<string, number | string>>(existing?.params ?? defaultsFor(def))
  const [color, setColor] = useState(existing?.color ?? def.colors[0])
  const [menu, setMenu] = useState<{ items: MenuItem[] } | null>(null)
  const save = (l: IndicatorConf[]) => t.setPrefs({ indicators: { ...t.prefs.indicators, [t.chartSym]: l } })
  const done = () => {
    if (existing) save(list.map((c) => (c.id === editId ? { ...c, params: { ...params }, color } : c)))
    else {
      const win = def.sub ? (w > 0 ? w : Math.max(0, ...list.map((c) => c.window)) + 1) : 0
      save([...list, { id: Math.random().toString(36).slice(2, 9), type, params: { ...params }, color, window: win }])
    }
    onDone()
  }
  return (
    <Page title={def.name} onBack={onBack} testId="indicator-params" actions={<>
      {existing && <IconBtn onClick={() => { save(list.filter((c) => c.id !== editId)); onDone() }} label="Delete" testId="ind-delete"><ITrash /></IconBtn>}
      <button className="ib" style={{ width: 'auto', padding: '0 14px', color: 'var(--blue)', fontWeight: 500 }} onClick={done} data-testid="ind-done">DONE</button>
    </>}>
      <div className="sect">PARAMETERS</div>
      {def.params.map((pd) => pd.options ? (
        <SettingRow key={pd.key} title={pd.label} sub={String(params[pd.key])} onClick={() => setMenu({ items: pd.options!.map((o) => ({ label: o, checked: params[pd.key] === o, onClick: () => setParams({ ...params, [pd.key]: o }) })) })} />
      ) : (
        <div key={pd.key} className="srow" style={{ cursor: 'default' }}>
          <span className="stx"><span className="st">{pd.label}</span></span>
          <input type="number" step={pd.step ?? 1} min={pd.min} max={pd.max} value={params[pd.key]} data-testid={`param-${pd.key}`}
                 onChange={(e) => setParams({ ...params, [pd.key]: e.target.value === '' ? '' : Number(e.target.value) })}
                 style={{ width: 90, background: 'transparent', border: 0, borderBottom: '1px solid var(--blue)', color: 'var(--text)', fontSize: 17, textAlign: 'right', outline: 'none' }} />
        </div>
      ))}
      {!def.params.length && <div className="seclbl">This indicator has no parameters.</div>}
      <div className="sect">STYLE</div>
      <div style={{ display: 'flex', gap: 12, padding: '14px 16px', flexWrap: 'wrap' }}>
        {SWATCHES.map((c) => <button key={c} className={`swatch${color === c ? ' on' : ''}`} onClick={() => setColor(c)} aria-label={c} aria-pressed={color === c} style={{ width: 34, height: 34, borderRadius: '50%', background: c, outline: color === c ? '3px solid var(--blue)' : 'none', outlineOffset: 2 }} />)}
      </div>
      {def.levels && <div className="seclbl">Levels: {def.levels.join(', ')}</div>}
      {menu && <Menu items={menu.items} onClose={() => setMenu(null)} at={{ top: 120, left: 40 }} wide />}
    </Page>
  )
}

// =================================================================================== chart settings
export function ChartSettingsPage({ onBack }: { onBack: () => void }) {
  const t = useT()
  const p = t.prefs
  const [menu, setMenu] = useState<MenuItem[] | null>(null)
  const tog = (k: keyof Prefs, title: string, sub?: string) => <SettingRow title={title} sub={sub} right={<Check on={!!p[k]} />} onClick={() => t.setPrefs({ [k]: !p[k] } as Partial<Prefs>)} testId={`cs-${String(k)}`} />
  const ask = p.showAsk ?? t.cfg.show_ask_line
  return (
    <Page title="Chart settings" onBack={onBack} testId="chart-settings">
      <div className="sect">CHART</div>
      {(['bars', 'candles', 'line'] as const).map((k) => (
        <SettingRow key={k} on={p.chartType === k} title={k === 'bars' ? 'Bar chart' : k === 'candles' ? 'Japanese candlesticks' : 'Line chart'} right={<Check on={p.chartType === k} />} onClick={() => t.setPrefs({ chartType: k })} testId={`cs-type-${k}`} />
      ))}
      <div className="sect">DISPLAY</div>
      {tog('showVolumes', 'Volumes', 'Show tick volumes')}
      {tog('showOHLC', 'OHLC', 'Show the open, high, low and close of the bar under the crosshair')}
      {tog('showTradeLevels', 'Trade levels', 'Show positions, orders, stop loss and take profit on the chart')}
      <SettingRow title="Ask price line" sub="Show the ask price line" right={<Check on={ask} />} onClick={() => t.setPrefs({ showAsk: !ask })} testId="cs-ask" />
      {tog('showSeparators', 'Period separators', 'Show the boundaries of days (months on daily and larger charts)')}
      {tog('showGrid', 'Grid', 'Show the grid')}
      <div className="sect">COLORS</div>
      <SettingRow title="Color scheme" sub={{ default: 'Default', green: 'Green on black', bw: 'Black on white' }[p.colorScheme]}
        onClick={() => setMenu([{ label: 'Default', checked: p.colorScheme === 'default', onClick: () => t.setPrefs({ colorScheme: 'default' }) },
          { label: 'Green on black', checked: p.colorScheme === 'green', onClick: () => t.setPrefs({ colorScheme: 'green' }) },
          { label: 'Black on white', checked: p.colorScheme === 'bw', onClick: () => t.setPrefs({ colorScheme: 'bw' }) }])} testId="cs-colors" />
      {menu && <Menu items={menu} onClose={() => setMenu(null)} at={{ top: 200, left: 40 }} wide />}
    </Page>
  )
}

// =================================================================================== drawer
export function Drawer({ onClose }: { onClose: () => void }) {
  const t = useT()
  const unread = MAILS.filter((_, i) => !t.prefs.mailRead.includes(i)).length
  const go = (tab: Parameters<typeof t.setTab>[0]) => { t.setTab(tab); onClose() }
  const item = (icon: React.ReactNode, label: string, onClick: () => void, extra?: React.ReactNode, id?: string) => (
    <button className="it" onClick={onClick} data-testid={`drawer-${id ?? label.toLowerCase().replace(/\s+/g, '-')}`}>{icon}<span>{label}</span>{extra}</button>
  )
  return (
    <>
      <div className="veil" onClick={onClose} />
      <div className="drw" data-testid="drawer">
        <div className="acc">
          <div className="logo">5</div>
          <div><b>{t.cfg.account_name}</b><span>{t.cfg.login} - {t.cfg.server_name}</span>
            <button className="manage" onClick={() => { onClose(); t.push({ id: 'accounts' }) }} data-testid="drawer-manage">Manage accounts</button></div>
          <span className="ribbon">Demo</span>
        </div>
        <div className="items">
          {item(<DTrade />, 'Trade', () => go('trade'))}
          {item(<DNews />, 'News', () => go('news'))}
          {item(<DMail />, 'Mailbox', () => go('mailbox'), unread ? <span className="badge">{unread}</span> : null)}
          {item(<DJournal />, 'Journal', () => go('journal'))}
          {item(<DSettings />, 'Settings', () => go('settings'))}
          {item(<DCalendar />, 'Economic calendar', () => go('calendar'), <span className="ads">Ads</span>)}
          {item(<DCommunity />, 'Traders Community', () => go('community'))}
          {item(<DAlgo />, 'MQL5 Algo Trading', () => go('algo'))}
          {item(<DHelp />, 'User guide', () => go('guide'))}
          {item(<DInfo />, 'About', () => go('about'))}
        </div>
      </div>
    </>
  )
}

// =================================================================================== settings
export function SettingsScreen() {
  const t = useT()
  const p = t.prefs
  const [menu, setMenu] = useState<{ items: MenuItem[]; top: number } | null>(null)
  const [pin, setPin] = useState<null | 'set' | 'confirm'>(null)
  const [pin1, setPin1] = useState('')
  const [pinText, setPinText] = useState('')
  const at = (e: React.MouseEvent) => Math.max(60, (e.currentTarget as HTMLElement).getBoundingClientRect().top - (document.querySelector('.mt5')!.getBoundingClientRect().top) - 40)
  const tog = (k: keyof Prefs, title: string, sub: string) => <SettingRow title={title} sub={sub} right={<Check on={!!p[k]} />} onClick={() => t.setPrefs({ [k]: !p[k] } as Partial<Prefs>)} testId={`set-${String(k)}`} />
  const choose = (e: React.MouseEvent, opts: string[], cur: string, set: (v: string) => void) => setMenu({ top: at(e), items: opts.map((o) => ({ label: o, checked: o === cur, onClick: () => set(o) })) })
  return (
    <div className="main" data-testid="settings-screen">
      <Header left="menu" onLeft={t.openDrawer} title="Settings" />
      <div className="scroll">
        <div className="sect">QUOTES</div>
        {tog('advanced', 'Advanced mode', 'In the advanced mode, the quotes window contains spreads, time data, as well as High and Low prices.')}
        {tog('orderSounds', 'Order sounds', 'Play sounds for orders')}
        <SettingRow title="One Click Trading" sub="Allows performing trade operations with a single tap without additional confirmation from the trader" right={<Check on={p.oneClick} />}
          onClick={() => t.setPrefs(p.oneClick ? { oneClick: false, oneClickPanel: false } : { oneClick: true })} testId="set-oneClick" />
        {tog('confirmOnChart', 'Confirmation of trades on chart', "Don't show a confirmation dialog when closing positions, modifying or deleting orders from a chart")}
        {tog('accountInMenu', 'Account info in bottom menu', 'Show account Balance on the Trade tab when no positions are open, otherwise show floating P/L.')}
        <div className="sect">MESSAGES</div>
        <SettingRow title="MetaQuotes ID" sub="Use this ID to send messages to this device via notify service." right={p.mqid}
          onClick={() => { void navigator.clipboard?.writeText(p.mqid).catch(() => undefined); t.toast(`MetaQuotes ID ${p.mqid} copied`) }} testId="set-mqid" />
        <SettingRow title="Vibration" sub={{ always: 'Always', silent: 'Only in silent mode', never: 'Never' }[p.vibration]}
          onClick={(e?: unknown) => choose(e as React.MouseEvent, ['Always', 'Only in silent mode', 'Never'], { always: 'Always', silent: 'Only in silent mode', never: 'Never' }[p.vibration],
            (v) => { t.setPrefs({ vibration: v === 'Always' ? 'always' : v === 'Never' ? 'never' : 'silent' }); if (v === 'Always') navigator.vibrate?.(60) })} testId="set-vibration" />
        <SettingRow title="Notification ringtone" sub={p.ringtone} onClick={(e?: unknown) => choose(e as React.MouseEvent, Object.keys(RINGTONES), p.ringtone, (v) => { t.setPrefs({ ringtone: v }); sounds.ring(v) })} testId="set-ringtone" />
        <SettingRow title="Content auto-download" sub={{ always: 'Always', wifi: 'Only over Wi-Fi', never: 'Never' }[p.autoDownload]}
          onClick={(e?: unknown) => choose(e as React.MouseEvent, ['Always', 'Only over Wi-Fi', 'Never'], { always: 'Always', wifi: 'Only over Wi-Fi', never: 'Never' }[p.autoDownload],
            (v) => t.setPrefs({ autoDownload: v === 'Always' ? 'always' : v === 'Never' ? 'never' : 'wifi' }))} testId="set-download" />
        <div className="sect">SECURITY</div>
        <SettingRow title="OTP" sub="One-time Password Generator" onClick={() => t.push({ id: 'otp' })} testId="set-otp" />
        <SettingRow title="Lock Screen" sub="Lock screen when the app is hidden and request pin or biometrics on start" right={<Check on={p.lockScreen} />}
          onClick={() => (p.lockScreen ? t.setPrefs({ lockScreen: false, pin: null }) : (setPin('set'), setPinText('')))} testId="set-lock" />
        <div className="sect">NEWS</div>
        {tog('news', 'Enable News', 'Receive news updates')}
        <div className="sect">INTERFACE</div>
        <SettingRow title="Language" sub={p.language} onClick={(e?: unknown) => choose(e as React.MouseEvent, ['System language', 'English'], p.language, (v) => t.setPrefs({ language: v }))} testId="set-language" />
        {tog('tablet', 'Tablet Interface', 'Enable tablet interface')}
        {tog('alwaysOn', 'Always on display', 'Prevent the screen from turning off while the app is active')}
        <SettingRow title="Choose theme" sub={{ light: 'Light', dark: 'Dark', black: 'Pure black' }[p.theme]}
          onClick={(e?: unknown) => choose(e as React.MouseEvent, ['Light', 'Dark', 'Pure black'], { light: 'Light', dark: 'Dark', black: 'Pure black' }[p.theme],
            (v) => t.setPrefs({ theme: v === 'Light' ? 'light' : v === 'Dark' ? 'dark' : 'black' }))} testId="set-theme" />
        <div style={{ height: 30 }} />
      </div>
      {menu && <Menu items={menu.items} at={{ top: menu.top, right: 16 }} onClose={() => setMenu(null)} wide />}
      {pin && (
        <Dialog title={pin === 'set' ? 'Set a PIN' : 'Repeat the PIN'} onClose={() => setPin(null)} testId="pin-dialog" actions={[{ label: 'Cancel', onClick: () => setPin(null) },
          { label: 'OK', primary: true, testId: 'pin-ok', onClick: async () => {
            if (!/^\d{4,8}$/.test(pinText)) { t.toast('Use 4 to 8 digits', true); return }
            if (pin === 'set') { setPin1(pinText); setPinText(''); setPin('confirm'); return }
            if (pinText !== pin1) { t.toast('The PINs do not match', true); setPin('set'); setPinText(''); return }
            t.setPrefs({ lockScreen: true, pin: await hashPin(pinText) }); setPin(null); t.toast('Lock screen enabled')
          } }]}>
          The app will ask for this PIN when it starts and when you come back to it.
          <input type="password" inputMode="numeric" autoFocus value={pinText} onChange={(e) => setPinText(e.target.value.replace(/\D/g, ''))} data-testid="pin-input" />
        </Dialog>
      )}
    </div>
  )
}

export function LockScreen({ onUnlock, hash }: { onUnlock: () => void; hash: string }) {
  const [v, setV] = useState('')
  const [err, setErr] = useState('')
  const press = async (d: string) => {
    const n = (v + d).slice(0, 8)
    setV(n); setErr('')
    if (n.length >= 4 && (await hashPin(n)) === hash) onUnlock()
    else if (n.length === 8) { setErr('Wrong PIN'); setV('') }
  }
  return (
    <div className="lock" data-testid="lock">
      <ILock size={40} />
      <div>Enter PIN</div>
      <div className="dots">{Array.from({ length: Math.max(4, v.length) }, (_, i) => <i key={i} className={i < v.length ? 'f' : ''} />)}</div>
      <div className="err">{err}</div>
      <div className="pad">
        {['1', '2', '3', '4', '5', '6', '7', '8', '9'].map((d) => <button key={d} onClick={() => press(d)} data-testid={`pin-${d}`}>{d}</button>)}
        <span /><button onClick={() => press('0')} data-testid="pin-0">0</button>
        <button onClick={() => setV(v.slice(0, -1))} aria-label="Delete" style={{ display: 'grid', placeItems: 'center' }}><IBackspace /></button>
      </div>
    </div>
  )
}

export function OtpPage({ onBack }: { onBack: () => void }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id) }, [])
  const step = Math.floor(now / 30000), left = 30 - Math.floor((now / 1000) % 30)
  const seed = (localStorage.getItem('mt5.otpseed') || (() => { const s = String(Math.random()).slice(2, 12); try { localStorage.setItem('mt5.otpseed', s) } catch { /* ignore */ } return s })())
  let h = 0
  for (const c of `${seed}:${step}`) h = (h * 33 + c.charCodeAt(0)) >>> 0
  const code = String(h % 1_000_000).padStart(6, '0')
  return (
    <Page title="One-time password" onBack={onBack} testId="otp">
      <div className="art" style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 44, letterSpacing: 8, color: 'var(--text)', margin: '30px 0 8px', fontVariantNumeric: 'tabular-nums' }} data-testid="otp-code">{code.slice(0, 3)} {code.slice(3)}</div>
        <div className="meta">Changes in {left} s</div>
        <p>This demo account does not require one-time passwords. A real broker links the generator to your account from the desktop terminal.</p>
      </div>
    </Page>
  )
}

// =================================================================================== drawer screens
export const MAILS = [
  { from: 'Terminal', subject: 'Welcome to your delayed demo terminal', body: 'This terminal works like MetaTrader 5, but every price you see is delayed by the time set on the server. Quotes, charts, orders, stop loss and take profit all run on that delayed clock, so what you practise here is exactly what you would see on a chart that lags the market.' },
  { from: 'Terminal', subject: 'How your demo account works', body: 'Your account is a paper account in USD with 1:100 leverage. Buys open at the ask and close at the bid, sells the other way round. Margin, equity, free margin and margin level are calculated like on a real MetaTrader 5 account. Use Trade -> New order, the order panel on the chart, or One Click Trading.' },
  { from: 'Terminal', subject: 'Trading from the chart', body: 'Tap the red and blue panel icon at the top right of the chart to show One Click Trading. Tap the round red/blue icon to open the order panel; while it is open you can drag the order price, stop loss and take profit lines directly on the chart.' },
]

export function MailboxScreen() {
  const t = useT()
  return (
    <div className="main" data-testid="mailbox-screen">
      <Header left="menu" onLeft={t.openDrawer} title="Mailbox"><IconBtn onClick={() => t.toast('Mail can only be received on this account')} label="Write"><IPlus /></IconBtn></Header>
      <div className="scroll list">
        {MAILS.map((m, i) => (
          <button key={i} className="li" onClick={() => { t.setPrefs({ mailRead: [...new Set([...t.prefs.mailRead, i])] }); t.push({ id: 'mail', idx: i }) }} data-testid={`mail-${i}`}>
            <DMail /><span className="lt"><b style={{ fontWeight: t.prefs.mailRead.includes(i) ? 400 : 700 }}>{m.subject}</b><small>{m.from}</small></span>
          </button>
        ))}
      </div>
    </div>
  )
}
export function MailPage({ idx, onBack }: { idx: number; onBack: () => void }) {
  const m = MAILS[idx]
  return <Page title="Mailbox" onBack={onBack} testId="mail-page"><div className="art"><h2>{m.subject}</h2><div className="meta">From: {m.from}</div><p>{m.body}</p></div></Page>
}

export function NewsScreen() {
  const t = useT()
  const [search, setSearch] = useState(false)
  return (
    <div className="main" data-testid="news-screen">
      <Header left="menu" onLeft={t.openDrawer} title={search ? <input autoFocus placeholder="Search news" style={{ background: 'transparent', border: 0, outline: 'none', fontSize: 18, width: '100%' }} /> : 'News'}>
        <IconBtn onClick={() => setSearch((v) => !v)} label="Search" testId="news-search">{search ? <IClose /> : <ISearch />}</IconBtn>
      </Header>
      <div className="scroll">
        {t.prefs.news ? <Empty icon={<DNews size={80} />} text="No news from this server" /> : <Empty icon={<DNews size={80} />} text="News is turned off in Settings"><button style={{ color: 'var(--blue)', marginTop: 8 }} onClick={() => t.setPrefs({ news: true })}>Enable News</button></Empty>}
      </div>
    </div>
  )
}

export function JournalScreen() {
  const t = useT()
  const entries = useSyncExternalStore(onJournal, () => JSON.stringify(journal().slice(-300)))
  const list = (JSON.parse(entries) as ReturnType<typeof journal>).slice().reverse()
  const [menu, setMenu] = useState(false)
  const d2 = (ms: number) => { const d = new Date(ms); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}.${String(d.getMilliseconds()).padStart(3, '0')}` }
  return (
    <div className="main" data-testid="journal-screen">
      <Header left="menu" onLeft={t.openDrawer} title="Journal" sub={dmy(Date.now(), t.cfg.tz)}>
        <IconBtn onClick={() => setMenu(true)} label="More" testId="journal-more"><svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="12" cy="19" r="2" /></svg></IconBtn>
      </Header>
      <div className="scroll">
        {list.map((e, i) => <div className="jr" key={i}><span>{d2(e.t)}</span><span className="src">{e.src}</span><span>{e.msg}</span></div>)}
        {!list.length && <Empty icon={<DJournal size={80} />} text="The journal is empty" />}
      </div>
      {menu && <Menu items={[{ label: 'Copy all', onClick: () => { void navigator.clipboard?.writeText(list.map((e) => `${d2(e.t)}\t${e.src}\t${e.msg}`).join('\n')).catch(() => undefined); t.toast('Journal copied') } },
        { label: 'Clear', onClick: () => clearJournal() }]} onClose={() => setMenu(false)} at={{ top: 52, right: 12 }} />}
    </div>
  )
}

export function CalendarScreen() {
  const t = useT()
  const now = Date.now() - t.cfg.delay_s * 1000
  const days = Array.from({ length: 7 }, (_, i) => now + (i - 1) * 86400_000)
  return (
    <div className="main" data-testid="calendar-screen">
      <Header left="menu" onLeft={t.openDrawer} title="Economic calendar"><IconBtn onClick={() => t.toast('Calendar filters: all countries, all importance')} label="Filter"><ISearch /></IconBtn></Header>
      <div className="scroll">
        {days.map((d) => (
          <div key={d}><div className="seclbl" style={{ fontWeight: 700, color: 'var(--text)' }}>{new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long', timeZone: t.cfg.tz }).format(d)}</div>
            <div className="li" style={{ color: 'var(--dim)', fontSize: 14, borderBottom: '1px solid var(--line)', padding: '10px 16px' }}>No events from this server</div></div>
        ))}
      </div>
    </div>
  )
}

export function LinkScreen({ kind }: { kind: 'community' | 'algo' | 'guide' | 'about' }) {
  const t = useT()
  const info = {
    community: { title: 'Traders Community', icon: <DCommunity size={72} />, text: 'The MQL5.community is the largest community of traders: articles, forum, signals, freelance and the market of trading robots.', url: 'https://www.mql5.com/', btn: 'Open MQL5.community' },
    algo: { title: 'MQL5 Algo Trading', icon: <DAlgo size={72} />, text: 'Algorithmic trading: trading robots, technical indicators and scripts for MetaTrader 5, written in MQL5.', url: 'https://www.mql5.com/en/market/mt5', btn: 'Open the Market' },
    guide: { title: 'User guide', icon: <DHelp size={72} />, text: 'How to use the mobile terminal: quotes, charts, trading, history, messages and settings.', url: 'https://www.metatrader5.com/en/mobile-trading/android/help', btn: 'Open the user guide' },
    about: { title: 'About', icon: <DInfo size={72} />, text: `Delayed demo terminal, built to look and work like MetaTrader 5 for Android so that practising here carries straight over to the real app. Prices run ${t.cfg.delay_s} s behind real time. Account ${t.cfg.login} on ${t.cfg.server_name}.`, url: '', btn: '' },
  }[kind]
  return (
    <div className="main" data-testid={`${kind}-screen`}>
      <Header left="menu" onLeft={t.openDrawer} title={info.title} />
      <div className="scroll art" style={{ textAlign: 'center' }}>
        <div style={{ color: 'var(--dim)', margin: '40px 0 16px' }}>{info.icon}</div>
        <p>{info.text}</p>
        {info.url && <a className="btn" href={info.url} target="_blank" rel="noreferrer">{info.btn}</a>}
        {kind === 'about' && <p className="meta" style={{ marginTop: 20 }}>Version 5.0 (lab build)<br />Quotes delay: {t.cfg.delay_s} s · Server time zone: {t.cfg.tz}</p>}
      </div>
    </div>
  )
}

export function AccountsPage({ onBack }: { onBack: () => void }) {
  const t = useT()
  const a = t.trade?.account
  const [menu, setMenu] = useState(false)
  return (
    <Page title="Accounts" onBack={onBack} testId="accounts" actions={<IconBtn onClick={() => t.toast('New accounts are opened by the server administrator')} label="Add account"><IPlus /></IconBtn>}>
      <div className="list">
        <div className="li" style={{ alignItems: 'flex-start', paddingTop: 14, paddingBottom: 14, cursor: 'pointer' }} onClick={() => setMenu(true)} data-testid="account-row">
          <div className="logo" style={{ width: 44, height: 44, borderRadius: '50%', background: 'conic-gradient(#e8453c 0 25%, #f5b800 0 50%, #36b24a 0 75%, #2f7ff0 0)', flex: 'none' }} />
          <span className="lt"><b>{t.cfg.account_name}</b><small>{t.cfg.login} - {t.cfg.server_name}</small><small>Demo · USD · 1:{a?.leverage ?? 100}</small>
            <small style={{ color: 'var(--text)', fontSize: 15, marginTop: 6 }}>{a ? a.balance.toFixed(2) : '–'} USD</small></span>
          <span style={{ color: 'var(--blue)', fontSize: 13 }}>connected</span>
        </div>
      </div>
      {menu && <Menu at={{ top: 120, left: 40 }} onClose={() => setMenu(false)} wide items={[
        { label: 'Login to trade account', onClick: () => t.toast(`Already connected to ${t.cfg.server_name}`) },
        { label: 'Change password', onClick: () => t.toast('Passwords are managed by the server administrator') },
        { label: 'Trade', onClick: () => { t.setTab('trade') } },
        { label: 'Delete account', disabled: true, onClick: () => undefined },
      ]} />}
    </Page>
  )
}

export function MessagesScreen() {
  const t = useT()
  const [search, setSearch] = useState(false)
  return (
    <div className="main" data-testid="messages-screen">
      <Header left="menu" onLeft={t.openDrawer} title={search ? <input autoFocus placeholder="Search" style={{ background: 'transparent', border: 0, outline: 'none', fontSize: 18, width: '100%' }} /> : 'Messages'}>
        {!search && <button onClick={() => t.toast(`MetaQuotes ID: ${t.prefs.mqid}`)} style={{ background: '#d8d8d8', color: '#222', fontSize: 11, fontWeight: 700, padding: '2px 5px', borderRadius: 3, marginRight: 8 }} data-testid="mqid">MQID</button>}
        <IconBtn onClick={() => setSearch((v) => !v)} label="Search" testId="msg-search">{search ? <IClose /> : <ISearch />}</IconBtn>
      </Header>
      <div className="scroll"><Empty icon={<EChat />} text="No messages" /></div>
      <div className="reg">
        <button className="p" onClick={() => window.open('https://www.mql5.com/en/auth_register', '_blank', 'noopener')} data-testid="msg-register">Register</button>
        <button className="s" onClick={() => window.open('https://www.mql5.com/en/auth_login', '_blank', 'noopener')} data-testid="msg-signin">Sign in</button>
      </div>
    </div>
  )
}

