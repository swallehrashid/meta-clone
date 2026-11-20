import { lazy, Suspense } from 'react'
import { createRoot } from 'react-dom/client'

// Two apps, one bundle: `/` is the MT5-style terminal (what you look at); `/admin` is the back office (delay, labels, peek,
// bot lab). The back office is lazy-loaded, so the terminal never downloads or even contains any of it.
const admin = location.pathname.replace(/\/+$/, '') === '/admin'
const Terminal = lazy(() => import('./terminal/Terminal'))
const Admin = lazy(() => import('./admin/AdminApp'))
createRoot(document.getElementById('root')!).render(<Suspense fallback={null}>{admin ? <Admin /> : <Terminal />}</Suspense>)

// Installable PWA: the service worker only registers in a secure context (https or localhost).
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => { navigator.serviceWorker.register('/sw.js').catch(() => { /* optional */ }) })
}
