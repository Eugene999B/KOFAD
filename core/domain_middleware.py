"""Keep company, customer and staff traffic on separate browser origins."""
import re

from django.http import HttpResponse, HttpResponseRedirect

ROOT = "kofadimpex.com"
MARKET = "market.kofadimpex.com"
STAFF = "staff.kofadimpex.com"
HOSTS = {ROOT, MARKET, STAFF, "www.kofadimpex.com", "kofad-web-production.up.railway.app"}


class OfficialDomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower().rstrip(".")
        if host not in HOSTS:
            return self.get_response(request)
        path = request.path
        # Preserve signed provider endpoints and public assets on saved origins.
        shared = (
            path in {"/health/", "/robots.txt"} or path.startswith("/static/")
            or path.startswith("/sms/callback/") or path == "/sms/delivery/"
            or path == "/whatsapp/webhook/"
            or path == "/market/payments/paystack/webhook/"
            or path == "/market/payments/hubtel/callback/"
        )
        # Staff delivery tools use these public, read-only location endpoints.
        staff_map = host == STAFF and request.method in {"GET", "HEAD"} and path.startswith(
            ("/market/location/", "/market/delivery/quote/")
        )
        public_image = request.method in {"GET", "HEAD"} and bool(re.fullmatch(
            r"/market/(?:products/[0-9]+/image|gallery/[0-9]+/image)/(?:thumb|full)/", path
        ))
        staff_support = host == STAFF and path.startswith(("/market/support/", "/market/returns/attachments/"))
        if shared or staff_map or public_image or staff_support:
            return self.get_response(request)
        target_path = request.get_full_path()
        if path == "/" and host in {MARKET, STAFF}:
            target_path = "/market/" if host == MARKET else "/workspace/"
            if request.META.get("QUERY_STRING"):
                target_path += "?" + request.META["QUERY_STRING"]
        if path == "/" and host not in {MARKET, STAFF}:
            target = ROOT
        elif target_path.startswith("/market/"):
            target = MARKET
        elif path in {
            "/about/", "/faq/", "/delivery/", "/returns-policy/", "/terms/",
            "/privacy/", "/contact/", "/robots.txt", "/sitemap.xml",
        } or path.startswith("/verify/worker/"):
            target = ROOT
        else:
            target = STAFF
        if host != target or target_path != request.get_full_path():
            if request.method not in {"GET", "HEAD"}:
                # Never forward credentials, cart writes or financial POST bodies
                # between origins. Reload the form on its canonical host.
                return HttpResponse("Open this page on its official KOFAD address and try again.", status=409)
            response = HttpResponseRedirect("https://" + target + target_path)
            response["Cache-Control"] = "no-store"
            return response
        return self.get_response(request)
