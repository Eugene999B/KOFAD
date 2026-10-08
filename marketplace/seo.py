"""Crawl entrypoints for KOFAD public domains; staff remains private."""
from xml.etree.ElementTree import Element, SubElement, tostring

from django.http import HttpResponse, HttpResponseNotFound
from django.views.decorators.http import require_safe

from .models import MarketListing

COMPANY_HOST = "kofadimpex.com"
MARKET_HOST = "market.kofadimpex.com"
COMPANY_PAGES = (
    "/", "/about/", "/faq/", "/delivery/", "/returns-policy/",
    "/terms/", "/privacy/", "/contact/",
)


def _host(request):
    return request.get_host().split(":")[0].lower().rstrip(".")


@require_safe
def robots(request):
    host = _host(request)
    if host == COMPANY_HOST:
        body = ("User-agent: *\nAllow: /\nDisallow: /technical-admin/\n"
                "Disallow: /workspace/\nDisallow: /settings/\n"
                "Disallow: /api/\nSitemap: https://kofadimpex.com/sitemap.xml\n")
    elif host == MARKET_HOST:
        body = (
            "User-agent: *\nAllow: /market/\n"
            "Disallow: /market/account/\nDisallow: /market/access/\n"
            "Disallow: /market/cart/\nDisallow: /market/checkout/\n"
            "Disallow: /market/orders/\nDisallow: /market/payments/\n"
            "Disallow: /market/support/\n"
            "Sitemap: https://market.kofadimpex.com/sitemap.xml\n"
        )
    else:
        # Staff origin and any non-canonical Railway host should not be indexed.
        body = "User-agent: *\nDisallow: /\n"
    response = HttpResponse(body, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    return response


@require_safe
def sitemap(request):
    host = _host(request)
    if host not in {COMPANY_HOST, MARKET_HOST}:
        response = HttpResponseNotFound("No public sitemap.")
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response
    root = Element("urlset", {"xmlns": "http://www.sitemaps.org/schemas/sitemap/0.9"})
    urls = (
        [f"https://{COMPANY_HOST}{path}" for path in COMPANY_PAGES]
        if host == COMPANY_HOST else [f"https://{MARKET_HOST}/market/"]
    )
    if host == MARKET_HOST:
        ids = MarketListing.objects.filter(enabled=True, product__active=True).order_by("pk").values_list("pk", flat=True).iterator(chunk_size=500)
        urls.extend(f"https://{MARKET_HOST}/market/products/{pk}/" for pk in ids)
    for url in urls:
        entry = SubElement(root, "url")
        SubElement(entry, "loc").text = url
    payload = b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="utf-8")
    response = HttpResponse(payload, content_type="application/xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=1800"
    return response
