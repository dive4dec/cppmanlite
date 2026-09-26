// cppmanlite service worker — offline-first C++ reference.
//
// DEPLOY-AGNOSTIC: everything is relative to the worker's own URL (self.location),
// so it works from any host or sub-path (/cppmanlite/, root, a LAN server, etc.)
// with zero hard-coded domains. Do NOT add absolute URLs here.
//
// Caches:
//   SHELL  (versioned)  — the app shell: index.html, manifest, a few icons.
//                         Re-precached when the SW version bumps; old shells pruned.
//   DATA   (fixed name) — index.json + docs/*.html + common/*. Persists across
//                         shell updates so a version bump never wipes offline pages.
//
// Strategy (all self-healing, so a content push does NOT require a SW bump):
//   navigation        -> cached index.html (SPA shell); network when fresh, offline falls back to cache
//   index.json        -> stale-while-revalidate (instant from cache, quietly refreshed when online)
//   docs/ & common/   -> network-first (always fresh online), cache-fallback (offline)
//   other static      -> cache-first on exact URL (so ?v= cache-busting still works)

const SHELL_CACHE = 'cppmanlite-shell-v1';
const DATA_CACHE = 'cppmanlite-data-v1';   // fixed name: survives shell version bumps

// What we precache at install (tiny, static-ish). Versioned assets (app.js?v=,
// style.css?v=) are intentionally NOT precached: they are cached on first load
// by their exact ?v= URL, so bumping ?v= needs no SW change.
const SHELL_URLS = [
  './index.html',
  './manifest.json',
  './favicon-32.png',
  './icon-192.png',
  './icon-512.png',
  './apple-touch-icon.png',
];

// Keep the docs/common runtime cache from growing without bound (FIFO on those
// paths only; index.json is protected and never evicted). ~400 small HTML/SVG
// pages ≈ a few tens of MB — plenty for a personal reference.
const MAX_DATA_ENTRIES = 400;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL_URLS)) // atomic: all or nothing
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== SHELL_CACHE && k !== DATA_CACHE) // drop old shells
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

// Best-effort FIFO trim for docs/common; never touches index.json.
async function trimDataCache(cache) {
  const keys = await cache.keys();
  if (keys.length <= MAX_DATA_ENTRIES) return;
  for (const key of keys) {
    if (keys.length <= MAX_DATA_ENTRIES) break;
    const p = new URL(key.url).pathname;
    if (p.startsWith('/docs/') || p.startsWith('/common/')) {
      await cache.delete(key);
      keys.splice(keys.indexOf(key), 1);
    }
  }
}

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // never touch cross-origin

  const path = url.pathname;

  // 1) Navigation (address-bar / app launch) -> SPA shell. Serve the cached
  //    index.html so ?page= / ?search= still work; app.js reads location.search
  //    from the real URL, not the response bytes.
  if (req.mode === 'navigate') {
    event.respondWith(
      caches.match('./index.html').then(
        (hit) =>
          hit ||
          fetch(req)
            .then((res) => {
              if (res.ok) {
                const copy = res.clone();
                caches.open(SHELL_CACHE).then((c) => c.put('./index.html', copy));
              }
              return res;
            })
            .catch(() => caches.match('./index.html'))
      ),
    );
    return;
  }

  // 2) Search index -> stale-while-revalidate. Instant from cache, and when
  //    online we quietly refresh the cache so it self-heals to the newest
  //    content without blocking the render.
  if (path === '/index.json' || path.endsWith('/index.json')) {
    event.respondWith(
      caches.open(DATA_CACHE).then((cache) =>
        cache.match(req).then((hit) => {
          const refresh = fetch(req)
            .then((res) => {
              if (res.ok) cache.put(req, res);
              return res;
            })
            .catch(() => null); // offline: ignore the refresh, use cache
          return hit || refresh;
        }),
      ),
    );
    return;
  }

  // 3) Doc pages + shared assets -> network-first (fresh online), cache
  //    fallback (offline). Populate the bounded DATA cache as we go.
  if (path.startsWith('/docs/') || path.startsWith('/common/')) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(DATA_CACHE).then((c) => {
              c.put(req, copy).then(() => trimDataCache(c));
            });
          }
          return res;
        })
        .catch(() =>
          caches.match(req).then(
            (hit) =>
              hit ||
              new Response(
                '<!doctype html><meta charset="utf-8"><title>Offline</title>' +
                  '<body style="font-family:system-ui,sans-serif;background:#1e1e2e;' +
                  'color:#cdd6f4;display:flex;height:100vh;align-items:center;' +
                  'justify-content:center;flex-direction:column;text-align:center">' +
                  '<h2>Offline</h2><p>This page has not been saved for offline yet.</p>' +
                  '<p>Reconnect to view it.</p>',
                { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } },
              ),
          ),
        ),
    );
    return;
  }

  // 4) Everything else same-origin (app.js?v=, style.css?v=, icons, …) ->
  //    cache-first on the exact URL. The ?v= query is part of the key, so
  //    bumping the version transparently fetches + caches the new file.
  event.respondWith(
    caches.match(req).then(
      (hit) =>
        hit ||
        fetch(req).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL_CACHE).then((c) => c.put(req, copy));
          }
          return res;
        }),
    ),
  );
});
