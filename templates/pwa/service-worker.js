// {% load static %}
const VERSION = 'v8.1'; // ⚠️ BUMP ON EVERY DEPLOY — browser detects updates via file bytes
const STATIC_CACHE = 'mp-static-' + VERSION;
const PAGES_CACHE  = 'mp-pages-'  + VERSION;
const OFFLINE_URL  = '/offline/';

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
// INSTALL — cache the static shell. No skipWaiting() so the
// update banner can detect a waiting SW.
// ─────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE))
  );
});

// ─────────────────────────────────────────────────────────────
// ACTIVATE — wipe old mp-* caches, claim clients.
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

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

// ─────────────────────────────────────────────────────────────
// FETCH — the only change that matters for speed:
//   * static   → stale-while-revalidate (already was)
//   * navigate → stale-while-revalidate (was network-first)
// Everything else is excluded exactly like v6.
// ─────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (url.pathname.startsWith('/admin/')) return;
  if (url.pathname.startsWith('/update')) return;
  if (url.pathname === '/service-worker.js') return;

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
    return;
  }

  if (req.mode === 'navigate') {
    event.respondWith(staleWhileRevalidate(req, PAGES_CACHE));
  }
});

// Answer from cache instantly, refresh in the background.
// Falls back to network when not cached, then to /offline/.
async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);

  const network = fetch(req)
    .then((res) => {
      // Skip redirects (e.g. bounced to login) and failures
      if (res && res.ok && !res.redirected) {
        cache.put(req, res.clone());
      }
      return res;
    })
    .catch(() => null);

  return cached || (await network) || caches.match(OFFLINE_URL);
}