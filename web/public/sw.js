// Minimal app-shell service worker. It caches ONLY static files so the app opens instantly and
// can be installed; live data (API, websockets) always goes to the network and is never cached.
const CACHE = 'lab-shell-v1'
const API = ['/api', '/labels', '/features', '/calls', '/ws', '/health']

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(['/', '/manifest.webmanifest', '/icons/icon-192.png'])).then(() => self.skipWaiting()))
})
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()))
})
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  if (e.request.method !== 'GET' || url.origin !== location.origin) return
  if (API.some((p) => url.pathname === p || url.pathname.startsWith(p + '/'))) return   // never cache data
  e.respondWith(
    fetch(e.request)
      .then((res) => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)) } return res })
      .catch(() => caches.match(e.request).then((m) => m || caches.match('/')))
  )
})
