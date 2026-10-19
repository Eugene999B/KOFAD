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
    eligible = public_host and request.path in PUBLIC_PATHS
    posthog_token = getattr(settings, "KOFAD_POSTHOG_PROJECT_TOKEN", "")
    return {
        "cookie_analytics_id": measurement_id if (
            eligible and re.fullmatch(r"G-[A-Z0-9]{4,20}", measurement_id)
        ) else "",
        "cookie_posthog_enabled": bool(
            eligible and getattr(settings, "KOFAD_POSTHOG_ENABLED", False)
            and re.fullmatch(r"phc_[A-Za-z0-9]{20,120}", posthog_token)
        ),
    }
