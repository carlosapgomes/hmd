/* HMD — Service Worker (change dashboard-notifications-pwa, D4/R4) */

const CACHE_NAME = "hmd-cache-v1";
const STATIC_ASSETS = [
  "/static/manifest.json",
  "/static/js/sw.js",
  "/static/css/app.css",
  "/static/icons/hmd-base.svg",
  "/static/icons/hmd-maskable.svg",
  "/static/icons/icon-72.png",
  "/static/icons/icon-96.png",
  "/static/icons/icon-128.png",
  "/static/icons/icon-144.png",
  "/static/icons/icon-152.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-384.png",
  "/static/icons/icon-512.png",
  "/static/icons/maskable-192.png",
  "/static/icons/maskable-512.png",
];

/* Install: pre-cache static assets */
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    })
  );
  self.skipWaiting();
});

/* Activate: clean old caches */
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) => {
      return Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      );
    })
  );
  self.clients.claim();
});

/* Fetch: network-first for static + HTML, never intercept writes */
self.addEventListener("fetch", (event) => {
  // Never intercept POST/PUT/DELETE form submissions. They must go directly to
  // Django with the browser's normal cookie/CSRF semantics.
  if (event.request.method !== "GET") {
    return;
  }

  const url = new URL(event.request.url);

  // Network-first for static assets to avoid stale JS after quick fixes.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          // Só cacheia respostas OK (P2 review): 404/500 momentâneos não
          // viram entrada de cache; e uma resposta não-cacheável (206/Vary)
          // não derruba a cadeia do respondWith.
          if (response.ok) {
            caches
              .open(CACHE_NAME)
              .then((cache) => cache.put(event.request, response.clone()))
              .catch(() => {});
          }
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Network-first for HTML pages
  if (event.request.mode === "navigate") {
    // Bypass for case PDF routes: the HMD paths end with "/pdf/<position>/"
    // (doctor/scheduler) or "/documents/<id>/" (intake: serve_document,
    // aberto em nova aba), so the check MUST be a substring match. A suffix
    // check on "/pdf/" never matches these URLs and would let the worker
    // intercept the navigation. Chromium's native PDF viewer does not render
    // when the navigation response is delivered via respondWith, leaving the
    // new tab blank; let the browser fetch natively.
    if (url.pathname.includes("/pdf/") || url.pathname.includes("/documents/")) {
      return;
    }
    event.respondWith(
      fetch(event.request).catch(() => {
        // Fallback intencionalmente inerte (P2 review): nenhuma rota coloca
        // "/" no cache — HTML autenticado NUNCA é gravado no Cache Storage
        // (estação compartilhada); offline de navegação mostra o erro nativo.
        return caches.match("/");
      })
    );
    return;
  }

  // Default: network-only for everything else
  return;
});
