const CACHE_NAME = 'homeflix-shell-v6';
// JS/CSS có ?v=<hash> trong URL nên được cache theo từng phiên bản khi tải lần đầu, không precache
const SHELL_URLS = ['/', '/static/logo.png', '/static/manifest.json'];

// Install: pre-cache app-shell
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(SHELL_URLS))
            .then(() => self.skipWaiting())
    );
});

// Activate: clean up old cache versions
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys.filter((key) => key !== CACHE_NAME)
                    .map((key) => {
                        console.log('[SW] Deleting old cache:', key);
                        return caches.delete(key);
                    })
            )
        ).then(() => self.clients.claim())
    );
});

// Fetch: route requests
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // BYPASS: video streams, API calls, non-GET — never touch cache
    if (url.pathname.includes('/proxy/') ||
        url.pathname.includes('/media/') ||
        url.pathname.includes('/api/') ||
        event.request.method !== 'GET') {
        return;
    }

    // NETWORK-FIRST: root HTML (always get latest when online)
    if (url.pathname === '/') {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
                    return response;
                })
                .catch(() => caches.match('/'))
        );
        return;
    }

    // CACHE-FIRST: static assets. JS/CSS có ?v=<hash> nên URL mới = nội dung mới.
    event.respondWith(
        caches.match(event.request).then((cached) => {
            if (cached) return cached;
            return fetch(event.request).then((response) => {
                if (!response.ok) return response;
                const clone = response.clone();
                caches.open(CACHE_NAME).then(async (cache) => {
                    if (url.searchParams.has('v')) {
                        // Bỏ các phiên bản cũ của cùng file
                        for (const req of await cache.keys()) {
                            const old = new URL(req.url);
                            if (old.pathname === url.pathname && old.search !== url.search) cache.delete(req);
                        }
                    }
                    cache.put(event.request, clone);
                });
                return response;
            });
        })
    );
});
