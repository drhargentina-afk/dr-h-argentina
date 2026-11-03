/**
 * Service Worker Oficial — Dr. H Argentina (Nodo Capa-País AR)
 * Versión de Plataforma: 1.4.0-AR-PWA
 *
 * Funciones principales:
 * 1. Instalación y cacheo del App Shell (index.html, app.js, manifest.json) para carga instantánea.
 * 2. Purga automática de cachés obsoletas al detectar una nueva versión desde /health.
 * 3. Estrategia Network-First para llamadas de API (/pre-quote, /analyze-expediente, /emergency-scan,
 *    /repository-consult, /cron-07am, /health) garantizando Zero-Storage de expedientes en caché.
 */

const CACHE_NAME = "dr-h-argentina-pwa-v1.4.0";
const APP_SHELL_ASSETS = [
  "/",
  "/index.html",
  "/app.js",
  "/manifest.json"
];

const API_PATHS_NEVER_CACHE = [
  "/pre-quote",
  "/analyze-expediente",
  "/download-report-pdf",
  "/emergency-scan",
  "/repository-consult",
  "/cron-07am",
  "/health",
  "/queue-status"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(APP_SHELL_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name.startsWith("dr-h-argentina-pwa-") && name !== CACHE_NAME)
          .map((oldName) => caches.delete(oldName))
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const reqUrl = new URL(event.request.url);

  // Garantía Zero-Storage: JAMÁS cachear peticiones POST ni endpoints procesales de la API
  if (
    event.request.method !== "GET" ||
    API_PATHS_NEVER_CACHE.some((apiPath) => reqUrl.pathname.startsWith(apiPath))
  ) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Estrategia Stale-While-Revalidate para el App Shell estático
  event.respondWith(
    caches.match(event.request).then((cachedResponse) => {
      const networkFetch = fetch(event.request)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const cloned = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, cloned);
            });
          }
          return networkResponse;
        })
        .catch(() => cachedResponse);

      return cachedResponse || networkFetch;
    })
  );
});
