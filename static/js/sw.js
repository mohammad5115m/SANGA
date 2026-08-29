/* SANGA service worker: offline shell only. Never cache inventory/pricing. */
const SHELL_CACHE = "sanga-shell-v4";
const SHELL_URLS = ["/offline/", "/static/manifest.webmanifest"];

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw error;
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_URLS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  // Never cache dynamic app/API/media responses that may include prices or stock.
  if (url.pathname.startsWith("/app/") || url.pathname.startsWith("/auth/") || url.pathname.startsWith("/media/")) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("/offline/"))
    );
    return;
  }

  // UI assets change often during local/PWA development. Cache-first left an
  // old app.css active after new inline SVG icons shipped, so those icons used
  // the browser's large default SVG size. Refresh same-origin static assets on
  // every online request and retain the latest successful copy for offline use.
  if (url.origin === self.location.origin && url.pathname.startsWith("/static/")) {
    event.respondWith(networkFirst(request));
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => cached || fetch(request))
  );
});

