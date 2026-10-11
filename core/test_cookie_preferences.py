from django.test import RequestFactory, SimpleTestCase, override_settings
from .cookie_preferences import context


@override_settings(KOFAD_GA4_MEASUREMENT_ID="G-ABC12345", ALLOWED_HOSTS=[".kofadimpex.com"])
class CookieAnalyticsBoundaries(SimpleTestCase):
    def test_analytics_is_only_available_on_public_non_identifying_pages(self):
        factory = RequestFactory()
        for host, path, expected in [
            ("market.kofadimpex.com", "/market/", "G-ABC12345"),
            ("kofadimpex.com", "/about/", "G-ABC12345"),
            ("market.kofadimpex.com", "/market/orders/private-reference/", ""),
            ("market.kofadimpex.com", "/market/account/", ""),
            ("staff.kofadimpex.com", "/", ""),
        ]:
            with self.subTest(host=host, path=path):
                request = factory.get(path, HTTP_HOST=host)
                self.assertEqual(context(request)["cookie_analytics_id"], expected)

    @override_settings(KOFAD_GA4_MEASUREMENT_ID='G-INVALID"><script>')
    def test_invalid_measurement_id_is_never_rendered(self):
        request = RequestFactory().get("/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(context(request)["cookie_analytics_id"], "")
