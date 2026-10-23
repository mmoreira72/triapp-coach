// Tri Squad service worker: network first for pages; the last copy of each board page stays available offline.
const VERSION = 'tri-v4';
const SHELL = ['/static/offline.html', '/static/icons/icon-192.png', '/static/icons/apple-touch-icon.png'];
const PAGES = 'tri-pages';

self.addEventListener('install', e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION && k !== PAGES).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET') return;
  if (url.hostname === 'cdnjs.cloudflare.com') {
    // Chart.js: without it a page from the offline copy cannot draw; versioned URL, so the first copy is good forever
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      const copy = res.clone();
      caches.open(VERSION).then(c => c.put(req, copy));
      return res;
    })));
    return;
  }
  if (url.origin !== location.origin) return;
  if (req.mode === 'navigate') {
    const keep = url.pathname === '/' || url.pathname.startsWith('/board');
    e.respondWith(fetch(req).then(res => {
      const final = new URL(res.url || req.url);
      if (keep && res.ok && (final.pathname === '/' || final.pathname.startsWith('/board'))) {
        // "/" redirects to the athlete's page: keep the page under both addresses, as a plain (not redirected) response,
        // so the app, which always opens "/", finds it offline
        res.clone().blob().then(body => caches.open(PAGES).then(c => {
          const plain = () => new Response(body, { status: 200, headers: { 'Content-Type': 'text/html; charset=utf-8' } });
          c.put(req.url, plain());
          if (res.redirected) c.put(final.href, plain());
        }));
      }
      return res;
    }).catch(() =>
      // one retry before giving up: right after the app opens (from a notification, or waking up) the first request can fail
      new Promise(r => setTimeout(r, 1200)).then(() => fetch(req)).catch(async () =>
        (await caches.match(req.url)) || caches.match('/static/offline.html'))));
    return;
  }
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req)));
  }
});

// sign-out clears the cached pages (they hold the athlete's data)
self.addEventListener('message', e => { if (e.data === 'clear') caches.delete(PAGES); });
