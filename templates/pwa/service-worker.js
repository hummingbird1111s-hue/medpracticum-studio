{% load static %}
const VERSION = 'v1'; // bump this number when you want everyone to drop old caches
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

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

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

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/admin/')) return;

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