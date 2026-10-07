"""Route public shortcuts onto one origin without sharing session cookies."""
from django.http import HttpResponsePermanentRedirect


class OfficialDomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower()
        aliases = {
            "www.kofadimpex.com": "/",
            "market.kofadimpex.com": "/market/",
            "staff.kofadimpex.com": "/workspace/",
        }
        # Provider webhooks and in-flight forms retain their original endpoint.
        if host in aliases and request.method in {"GET", "HEAD"}:
            path = request.get_full_path()
            if request.path == "/":
                path = aliases[host]
                if request.META.get("QUERY_STRING"):
                    path += "?" + request.META["QUERY_STRING"]
            return HttpResponsePermanentRedirect("https://kofadimpex.com" + path)
        return self.get_response(request)
