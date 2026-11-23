// {% load static %}
const VERSION = 'v6'; // ✅ bumped from 'v5' — this deploy removes skipWaiting() so the update prompt can actually appear.
                     // ⚠️ BUMP THIS ON EVERY DEPLOY. The browser uses the file content to detect updates;
                     //    if only Django code changes and this file is byte-identical, no update prompt fires.
const STATIC_CACHE = 'mp-static-' + VERSION;
const PAGES_CACHE = 'mp-pages-' + VERSION;
const OFFLINE_URL = '/offline/';
const CACHED_PAGES = ['/', '/logs/new/']; // pages students need with no signal

const PRECACHE = [
  OFFLINE_URL,
  '{% static "css/tailwind.css" %}',
  '{% static "vendor/alpine.min.js" %}',
  '{% static "vendor/htmx.min.js" %}',
  '{% static "js/offline-sync.js" %}',
  '{% static "js/log_form.js" %}',
  '{% static "js/pwa.js" %}',
  '{% static "icons/icon-192.png" %}',
];

// ─────────────────────────────────────────────────────────────
// INSTALL — cache the shell. Do NOT skipWaiting(), or the update
// banner in base.html can never detect a waiting SW.
// ─────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
    // NOTE: no self.skipWaiting() here on purpose.
  );
});

// ─────────────────────────────────────────────────────────────
// ACTIVATE — clean up any old "mp-*" caches from previous versions
// and claim clients (harmless once we're actually activated).
// ─────────────────────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((k) => k.startsWith('mp-') && k !== STATIC_CACHE && k !== PAGES_CACHE)
          .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// ─────────────────────────────────────────────────────────────
// MESSAGE — support an explicit SKIP_WAITING trigger.
// The /update/ page in this build purges caches + unregisters,
// so this handler is defensive (not strictly required), but it
// lets a future "quick update" path activate immediately.
// ─────────────────────────────────────────────────────────────
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// ─────────────────────────────────────────────────────────────
// FETCH — same routing as before. Never touch /api/, /admin/,
// or the /update/ page (which must always come from network).
// ─────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin/')) return;
  if (url.pathname.startsWith('/update')) return;          // always fresh
  if (url.pathname === '/service-worker.js') return;       // never cache the SW itself

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }

  if (req.mode === 'navigate') {
    event.respondWith(networkFirstPage(req, url));
  }
});

// Static files: answer instantly from cache, refresh the cache in the background.
async function staleWhileRevalidate(req) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(req);
  const network = fetch(req)
    .then((res) => {
      if (res.ok) cache.put(req, res.clone());
      return res;
    })
    .catch(() => null);
  return cached || (await network) || Response.error();
}

// Pages: try the server first; if it fails, fall back to the last saved copy.
async function networkFirstPage(req, url) {
  const cache = await caches.open(PAGES_CACHE);
  try {
    const res = await fetch(req);
    // Never store redirects (e.g. bounced to the login page)
    if (res.ok && !res.redirected && CACHED_PAGES.includes(url.pathname)) {
      cache.put(url.pathname, res.clone());
    }
    return res;
  } catch (err) {
    const cached = await cache.match(url.pathname);
    if (cached) return cached;
    const offline = await caches.match(OFFLINE_URL);
    return offline || Response.error();
  }
}