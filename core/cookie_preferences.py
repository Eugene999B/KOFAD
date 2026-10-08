"""Optional analytics configuration; staff and private account pages stay excluded."""
import re
from django.conf import settings


PUBLIC_PATHS = {
    "/", "/market/", "/about/", "/faq/", "/contact/", "/delivery/",
    "/returns-policy/", "/terms/", "/privacy/",
}


def context(request):
    measurement_id = getattr(settings, "KOFAD_GA4_MEASUREMENT_ID", "")
    public_host = request.get_host().split(":")[0] in {"kofadimpex.com", "www.kofadimpex.com", "market.kofadimpex.com"}
    return {"cookie_analytics_id": measurement_id if (
        public_host and request.path in PUBLIC_PATHS
        and re.fullmatch(r"G-[A-Z0-9]{4,20}", measurement_id)
    ) else ""}
