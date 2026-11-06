const CACHE = 'deeptutor-offline-shell-v3'
const ASSETS = ['/offline/', '/offline/index.html', '/offline/app.js', '/offline/style.css']
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting()),
  )
})
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith('deeptutor-offline-shell-') && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  )
})
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  if (
    event.request.method !== 'GET' ||
    url.origin !== self.location.origin ||
    !ASSETS.includes(url.pathname)
  )
    return
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(url.pathname).then((value) => value || Response.error()),
    ),
  )
})
