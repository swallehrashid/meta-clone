import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { applyCssVars } from '../theme'
import '../styles.css'
import './admin.css'
import { AuthError, login, logout, me } from './adminApi'
import { BotLab } from './BotLab'
import LabView from './LabView'
import { TerminalPanel } from './TerminalPanel'

applyCssVars()
type Tab = 'terminal' | 'bots' | 'chart'
const TABS: { id: Tab; label: string }[] = [{ id: 'terminal', label: 'Terminal' }, { id: 'bots', label: 'Bot lab' }, { id: 'chart', label: 'Chart lab' }]

/** The back office: sign in, then control what the terminal shows (delay, pairs, ...), train / replay / run bots, and use the
 *  full chart lab (labels, Peek, practice calls). The public terminal at `/` knows nothing about any of this. */
export default function AdminApp() {
  const [state, setState] = useState<'loading' | 'out' | 'in'>('loading')
  const [user, setUser] = useState('')
  const [tab, setTabState] = useState<Tab>(() => { try { return (sessionStorage.getItem('admin.tab') as Tab) || 'terminal' } catch { return 'terminal' } })
  const setTab = (t: Tab) => { setTabState(t); try { sessionStorage.setItem('admin.tab', t) } catch { /* private mode */ } }

  useEffect(() => { me().then((r) => { setUser(r.username ?? 'admin'); setState(r.admin ? 'in' : 'out') }).catch(() => setState('out')) }, [])
  const signedOut = useCallback(() => setState('out'), [])

  if (state === 'loading') return <div className="boot dim">Loading…</div>
  if (state === 'out') return <Login onDone={(u) => { setUser(u); setState('in') }} />
  return (
    <div className="admin" data-testid="admin">
      <header className="admin-bar">
        <b className="brand">Lab back office</b>
        <nav>{TABS.map((t) => <button key={t.id} className={tab === t.id ? 'on' : ''} onClick={() => setTab(t.id)} data-testid={`tab-${t.id}`}>{t.label}</button>)}</nav>
        <span className="sp" />
        <a href="/" target="_blank" rel="noreferrer">Open terminal ↗</a>
        <span className="dim small">{user}</span>
        <button onClick={() => logout().finally(signedOut)}>Sign out</button>
      </header>
      <div className="admin-body">
        {tab === 'terminal' && <TerminalPanel onAuth={signedOut} />}
        {tab === 'bots' && <BotLab onAuth={signedOut} />}
        {tab === 'chart' && <LabView />}
      </div>
    </div>
  )
}

function Login({ onDone }: { onDone: (user: string) => void }) {
  const [u, setU] = useState('admin')
  const [pw, setPw] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await login(u, pw); onDone(u) } catch (x) { setErr(x instanceof AuthError ? 'Wrong username or password' : (x as Error).message) } finally { setBusy(false) }
  }
  return (
    <form className="login" onSubmit={submit} data-testid="login">
      <h1>Back office</h1>
      <p className="dim small">Sign in to set the terminal's delay, run bots and train them. The password was printed in the server console on first start
        (change it with <code>python -m tools.set_admin_password</code>).</p>
      <label>Username<input value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" data-testid="login-user" /></label>
      <label>Password<input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="current-password" autoFocus data-testid="login-pass" /></label>
      {err && <div className="err" data-testid="login-error">{err}</div>}
      <button className="primary" disabled={busy || !pw} data-testid="login-submit">{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
  )
}
