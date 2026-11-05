// Service worker: cache only the static app shell so the UI survives a brief
// WiFi blip. Dynamic paths (/api, /ws, /thumbs) are never cached.
const CACHE = "remprez-shell-v1";
const SHELL = [
  "./",
  "index.html",
  "styles.css",
  "app.js",
  "manifest.webmanifest",
  "icons/icon-192.png",
  "icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Never intercept data/control traffic.
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/ws") || url.pathname.startsWith("/thumbs")) {
    return;
  }
  if (e.request.method !== "GET") return;
  // Cache-first for the shell, falling back to network.
  e.respondWith(
    caches.match(e.request).then((hit) => hit || fetch(e.request))
  );
});
