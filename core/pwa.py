"""Installable KOFAD Market and Staff browser editions.

Service workers cache ONLY a fixed, non-personalized offline screen. They never
cache authenticated HTML, payments, receipts, APIs, employee/customer data, or
outgoing requests. Both channels have distinct origins, manifests and cache IDs.
"""
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


CHANNELS = {
    "customer": {
        "name": "KOFAD Market",
        "short_name": "KOFAD Market",
        "description": "Shop securely with KOFAD IMPEX ENTERPRISE.",
        "start": "/market/",
        "scope": "/market/",
        "script": "/market/app/sw.js",
        "offline": "/market/app/offline/",
        "background_color": "#f3f7f7",
        "theme_color": "#103c50",
    },
    "staff": {
        "name": "KOFAD Staff",
        "short_name": "KOFAD Staff",
        "description": "Authorized KOFAD business workspace. Sign-in required.",
        "start": "/workspace/",
        "scope": "/",
        "script": "/staff/app/sw.js",
        "offline": "/staff/app/offline/",
        "background_color": "#f3f7f7",
        "theme_color": "#103c50",
    },
}


def _channel(kind):
    if kind not in CHANNELS:
        raise ValueError("Unsupported PWA identity")
    return CHANNELS[kind]


@require_GET
def manifest(request, kind):
    c = _channel(kind)
    payload = {
        "id": c["start"],
        "name": c["name"],
        "short_name": c["short_name"],
        "description": c["description"],
        "start_url": c["start"],
        "scope": c["scope"],
        "display": "standalone",
        "display_override": ["standalone"],
        "orientation": "any",
        "background_color": c["background_color"],
        "theme_color": c["theme_color"],
        "icons": [
            {"src": staticfiles_storage.url("brand/favicon-192.png"),
             "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": staticfiles_storage.url("brand/favicon-512.png"),
             "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
        "categories": ["shopping"] if kind == "customer" else ["business"],
    }
    r = JsonResponse(payload, content_type="application/manifest+json")
    r["Cache-Control"] = "public, max-age=300"
    if kind == "staff":
        r["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return r


@require_GET
def worker(request, kind):
    c = _channel(kind)
    # Built from constant server-side paths; user inputs cannot inject JS.
    offline_path = c["offline"]
    cache_name = "kofad-offline-" + kind + "-v1"
    source = f"""/* KOFAD {kind} offline navigation only; NEVER cache private pages or APIs. */
const CACHE_NAME = "{cache_name}";
const OFFLINE_URL = "{offline_path}";
self.addEventListener("install", event => {{
  event.waitUntil(caches.open(CACHE_NAME).then(cache =>
    cache.add(new Request(OFFLINE_URL, {{cache: "reload"}}))
  ));
}});
self.addEventListener("message", event => {{
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
}});
self.addEventListener("activate", event => {{
  event.waitUntil(Promise.all([
    caches.keys().then(keys => Promise.all(keys
      .filter(key => key.startsWith("kofad-offline-{kind}-") && key !== CACHE_NAME)
      .map(key => caches.delete(key)))),
    self.clients.claim()
  ]));
}});
self.addEventListener("fetch", event => {{
  const request = event.request;
  if (request.method !== "GET" || request.mode !== "navigate") return;
  if (new URL(request.url).origin !== self.location.origin) return;
  event.respondWith(fetch(request).catch(async () =>
    (await caches.match(OFFLINE_URL)) || Response.error()
  ));
}});
"""
    r = HttpResponse(source, content_type="application/javascript; charset=utf-8")
    r["Cache-Control"] = "no-cache, no-store, must-revalidate"
    r["Service-Worker-Allowed"] = c["scope"]
    r["X-Content-Type-Options"] = "nosniff"
    if kind == "staff":
        r["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return r


@require_GET
def offline(request, kind):
    c = _channel(kind)
    # Static, deliberately unpersonalized. Displayed only when offline.
    html = ("""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#103c50">
<title>Connection needed | KOFAD</title>
<style>body{font:16px/1.65 system-ui,-apple-system,sans-serif;max-width:520px;
margin:0 auto;padding:12vh 22px;background:#f3f7f7;color:#17374b}
section{background:#fff;border:1px solid #d7e8e7;border-radius:20px;padding:27px}
strong{display:block;color:#117c78;letter-spacing:.14em;font-size:12px}
h1{font-size:30px;line-height:1.2}p{color:#5e7681}
button{background:#104b5e;border:0;color:#fff;border-radius:12px;
padding:14px 22px;font:inherit;font-weight:800}</style>
<section><strong>KOFAD IMPEX ENTERPRISE</strong>
<h1>Internet connection needed</h1>
<p>This installed app is offline. No personal information, orders, payments
or business records were stored for offline use. Reconnect before continuing.</p>
<button type="button" onclick="location.reload()">Try again</button>
</section></html>""")
    r = HttpResponse(html, content_type="text/html; charset=utf-8")
    # Explicitly public content with no personal or staff detail.
    r["Cache-Control"] = "public, max-age=3600"
    r["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return r


@require_GET
def market_install(request):
    from marketplace.views import _market_context
    from core.native_apps import app_metadata
    return render(request, "native_apps/web_install.html",
                  _market_context(request, title="Install KOFAD Market",
                                  app=app_metadata("customer")))
