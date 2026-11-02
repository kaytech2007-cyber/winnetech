/* ═══════════════════════════════════════════════════════════
   WTI Service Worker — PWA Offline Support
   ═══════════════════════════════════════════════════════════ */

const CACHE_NAME = 'wti-staff-v2';
const STATIC_CACHE = 'wti-static-v1';

// Core assets to cache for offline use
const PRECACHE_URLS = [
  '/static/css/main.css',
  '/static/js/main.js',
  '/static/manifest.json',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css',
  'https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap',
];

// ── Install: pre-cache core assets ──────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => {
        // Cache what we can, ignore failures for external resources
        return Promise.allSettled(
          PRECACHE_URLS.map(url =>
            cache.add(url).catch(() => {/* ignore individual failures */})
          )
        );
      })
      .then(() => self.skipWaiting())
  );
});

// ── Activate: clean old caches ───────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter(name => name !== CACHE_NAME && name !== STATIC_CACHE)
          .map(name => caches.delete(name))
      );
    }).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first with cache fallback ─────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET, WebSocket, and admin Django requests
  if (request.method !== 'GET') return;
  if (url.pathname.startsWith('/ws/')) return;
  if (url.pathname.startsWith('/admin/')) return;
  if (
    url.pathname.startsWith('/admin-dashboard/') ||
    url.pathname.startsWith('/superadmin/') ||
    url.pathname.startsWith('/hod/') ||
    url.pathname.startsWith('/facilitator/') ||
    url.pathname.startsWith('/teacher/') ||
    url.pathname.startsWith('/accounts/') ||
    url.pathname.startsWith('/private/')
  ) return;

  // For API calls — network only (no caching)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(request).catch(() => new Response(
        JSON.stringify({ error: 'offline', notifications: [], schedules: [] }),
        { headers: { 'Content-Type': 'application/json' } }
      ))
    );
    return;
  }

  // For static assets — cache first
  if (
    url.pathname.startsWith('/static/') ||
    url.hostname.includes('cdn.jsdelivr.net') ||
    url.hostname.includes('cdnjs.cloudflare.com') ||
    url.hostname.includes('fonts.googleapis.com') ||
    url.hostname.includes('fonts.gstatic.com') ||
    url.hostname.includes('unpkg.com')
  ) {
    event.respondWith(
      caches.match(request).then(cached => {
        if (cached) return cached;
        return fetch(request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(STATIC_CACHE).then(cache => cache.put(request, clone));
          }
          return response;
        }).catch(() => cached || new Response('', { status: 503 }));
      })
    );
    return;
  }

  // For HTML pages — network first, cache fallback
  event.respondWith(
    fetch(request)
      .then(response => {
        const cacheControl = response.headers.get('Cache-Control') || '';
        if (/private|no-store|no-cache/i.test(cacheControl)) {
          caches.open(CACHE_NAME).then(cache => cache.delete(request));
        }
        return response;
      })
      .catch(() => {
        if (request.mode === 'navigate') return offlinePage();
        return new Response('', { status: 503 });
      })
  );
});

function offlinePage() {
  return new Response(`
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>Offline — WTI</title>
      <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
          font-family: 'Poppins', Arial, sans-serif;
          background: #0a1628;
          color: #fff;
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          padding: 2rem;
        }
        .icon { font-size: 4rem; margin-bottom: 1rem; }
        h2 { font-size: 1.5rem; margin-bottom: 0.5rem; color: #f5a623; }
        p { color: rgba(255,255,255,0.7); font-size: 0.9rem; margin-bottom: 1.5rem; }
        button {
          background: linear-gradient(135deg, #f5a623, #e8920a);
          color: #fff; border: none; border-radius: 50px;
          padding: 0.8rem 2rem; font-size: 0.9rem;
          font-weight: 600; cursor: pointer;
        }
      </style>
    </head>
    <body>
      <div>
        <div class="icon">📡</div>
        <h2>You're Offline</h2>
        <p>No internet connection. Please check your network and try again.</p>
        <button onclick="window.location.reload()">Try Again</button>
      </div>
    </body>
    </html>
  `, { headers: { 'Content-Type': 'text/html' } });
}

// ── Push Notifications ───────────────────────────────────────
self.addEventListener('push', (event) => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || 'WTI Notification', {
      body: data.message || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-72.png',
      vibrate: [200, 100, 200],
      data: { url: data.url || '/' },
      actions: [
        { action: 'open', title: 'Open' },
        { action: 'dismiss', title: 'Dismiss' }
      ]
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  if (event.action === 'dismiss') return;
  const url = event.notification.data?.url || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then(clientList => {
      for (const client of clientList) {
        if (client.url === url && 'focus' in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});
