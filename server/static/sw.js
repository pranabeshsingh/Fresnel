// Fresnel 5G Cloud Hub Service Worker
const CACHE_NAME = 'fresnel-v11';
const STATIC_ASSETS = [
  '/',
  '/manifest.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Ignore non-http(s) schemes: chrome-extension://, data:, etc.
  // cache.put() only supports http/https — crashing here also breaks API fetches.
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return;

  // Network first for all API and SSE streams
  if (url.pathname.startsWith('/api/') || url.pathname.includes('stream')) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Stale-while-revalidate for static assets
  event.respondWith(
    caches.match(event.request).then((cached) => {
      const fetchPromise = fetch(event.request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const resClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            // Extra guard: only cache http/https (belt-and-suspenders)
            if (event.request.url.startsWith('http')) {
              cache.put(event.request, resClone).catch(() => {});
            }
          });
        }
        return networkResponse;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
