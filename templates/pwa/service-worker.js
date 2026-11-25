// {% load static %}
const VERSION = 'v8.5'; // ⚠️ BUMP ON EVERY DEPLOY — browser detects updates via file bytes
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
// FETCH — three-tier strategy:
//
//   1. NETWORK-ONLY (skip interception entirely)
//      Anything stateful, sensitive, or per-request. If we cache
//      these, PWA breaks — which is exactly what was happening
//      with /api/notifications/ before: cached forever, so no
//      toast/bell/badge ever fired.
//
//   2. STALE-WHILE-REVALIDATE
//      /static/ only — CSS/JS/images. Instant from cache, refresh
//      in background. Cache-busting handled by VERSION constant.
//
//   3. NETWORK-FIRST
//      All HTML navigations. Fresh page every time when online,
//      cache fallback when offline. THIS IS THE FIX — before, we
//      used SWR for navigations, meaning a user who deployed new
//      JS saw the OLD JS in their installed PWA for days.
// ─────────────────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  let url;
  try {
    url = new URL(req.url);
  } catch (e) {
    return;
  }

  // ── Tier 1: NETWORK-ONLY. Do NOT call respondWith. ──
  if (url.origin !== self.location.origin) return;              // external
  if (url.pathname.startsWith('/api/')) return;                  // data APIs
  if (url.pathname.startsWith('/admin')) return;                 // Django admin
  if (url.pathname.startsWith('/admin-workspace')) return;       // custom admin pages
  if (url.pathname.startsWith('/accounts')) return;              // auth flows
  if (url.pathname.startsWith('/update')) return;                // update page
  if (url.pathname === '/service-worker.js') return;             // self
  if (url.pathname.startsWith('/media/')) return;                // user uploads

  // ── Tier 2: STALE-WHILE-REVALIDATE for static assets ──
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(req, STATIC_CACHE));
    return;
  }

  // ── Tier 3: NETWORK-FIRST for HTML navigations ──
  if (req.mode === 'navigate') {
    event.respondWith(networkFirst(req, PAGES_CACHE, OFFLINE_URL));
    return;
  }
});

// ─────────────────────────────────────────────────────────────
// Strategy A: stale-while-revalidate (static assets)
//   Serve cache instantly, refresh in background. Cache busting
//   happens via VERSION constant — old caches die on activate.
// ─────────────────────────────────────────────────────────────
async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);

  const network = fetch(req)
    .then((res) => {
      if (res && res.ok && !res.redirected) {
        cache.put(req, res.clone());
      }
      return res;
    })
    .catch(() => null);

  return cached || (await network) || caches.match(OFFLINE_URL);
}

// ─────────────────────────────────────────────────────────────
// Strategy B: network-first (HTML navigations)
//   Fetch fresh HTML every time. Only cache successful,
//   non-redirected responses (so we never cache a login
//   redirect that later shows for a logged-in user). On
//   network failure, fall back to cache, then to /offline/.
// ─────────────────────────────────────────────────────────────
async function networkFirst(req, cacheName, offlineUrl) {
  const cache = await caches.open(cacheName);
  try {
    const res = await fetch(req);
    if (res && res.ok && !res.redirected) {
      cache.put(req, res.clone());
    }
    return res;
  } catch (err) {
    const cached = await cache.match(req);
    if (cached) return cached;
    const offline = await caches.match(offlineUrl);
    return offline || new Response('Offline — please check your connection.', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }
}