from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from .domain_middleware import OfficialDomainMiddleware


@override_settings(ALLOWED_HOSTS=["kofadimpex.com", "www.kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com", "testserver"])
class OfficialDomainTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = OfficialDomainMiddleware(lambda request: HttpResponse("unchanged"))

    def test_shortcuts_and_query_strings(self):
        for host, target in (
            ("www.kofadimpex.com", "/"),
            ("market.kofadimpex.com", "/market/"),
            ("staff.kofadimpex.com", "/workspace/"),
        ):
            response = self.middleware(self.factory.get("/?q=rice", HTTP_HOST=host))
            self.assertEqual(response.status_code, 301)
            self.assertEqual(response["Location"], "https://kofadimpex.com" + target + "?q=rice")

    def test_deep_links_preserved(self):
        response = self.middleware(self.factory.get("/market/orders/?page=2", HTTP_HOST="www.kofadimpex.com"))
        self.assertEqual(response["Location"], "https://kofadimpex.com/market/orders/?page=2")

    def test_post_callbacks_and_primary_origin_are_not_redirected(self):
        for request in (
            self.factory.post("/market/payment/webhook/", HTTP_HOST="market.kofadimpex.com"),
            self.factory.get("/", HTTP_HOST="kofadimpex.com"),
            self.factory.get("/", HTTP_HOST="testserver"),
        ):
            self.assertEqual(self.middleware(request).content, b"unchanged")
