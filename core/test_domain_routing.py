from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from .domain_middleware import HOSTS, OfficialDomainMiddleware


@override_settings(ALLOWED_HOSTS=[*HOSTS, "testserver"])
class OfficialDomainTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = OfficialDomainMiddleware(lambda request: HttpResponse("served"))

    def test_canonical_hosts_keep_their_pages(self):
        for host, path in (("kofadimpex.com", "/"), ("market.kofadimpex.com", "/market/"),
                           ("staff.kofadimpex.com", "/workspace/")):
            response = self.middleware(self.factory.get(path, HTTP_HOST=host))
            self.assertEqual(response.content, b"served")

    def test_shortcuts_and_old_links_choose_correct_origin(self):
        for host, path, target in (
            ("market.kofadimpex.com", "/?q=rice", "market.kofadimpex.com/market/?q=rice"),
            ("staff.kofadimpex.com", "/", "staff.kofadimpex.com/workspace/"),
            ("kofadimpex.com", "/login/?next=/account/", "staff.kofadimpex.com/login/?next=/account/"),
            ("www.kofadimpex.com", "/", "kofadimpex.com/"),
            ("staff.kofadimpex.com", "/market/", "market.kofadimpex.com/market/"),
            ("market.kofadimpex.com", "/administration/", "staff.kofadimpex.com/administration/"),
        ):
            response = self.middleware(self.factory.get(path, HTTP_HOST=host))
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Location"], "https://" + target)

    def test_wrong_host_writes_are_rejected_without_forwarding(self):
        for host, path in (("market.kofadimpex.com", "/api/trades/"),
                           ("kofadimpex.com", "/login/"), ("staff.kofadimpex.com", "/market/cart/update/")):
            response = self.middleware(self.factory.post(path, HTTP_HOST=host))
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("Location", response)

    def test_callbacks_assets_and_health_are_not_redirected(self):
        for host in HOSTS:
            for path in ("/health/", "/static/app.js", "/sms/delivery/",
                         "/market/payments/paystack/webhook/", "/whatsapp/webhook/"):
                response = self.middleware(self.factory.post(path, HTTP_HOST=host))
                self.assertEqual(response.content, b"served")

    def test_development_and_staff_map_requests_still_work(self):
        for host, path in (("testserver", "/login/"),
                           ("staff.kofadimpex.com", "/market/location/search/?q=Accra")):
            self.assertEqual(self.middleware(self.factory.get(path, HTTP_HOST=host)).content, b"served")

    def test_staff_shared_tools_use_staff_authentication_on_staff_host(self):
        for path in ("/market/products/1/image/thumb/", "/market/gallery/1/image/full/",
                     "/market/support/conversations/1/typing/", "/market/returns/attachments/1/"):
            method = self.factory.post if path.endswith("/typing/") else self.factory.get
            response = self.middleware(method(path, HTTP_HOST="staff.kofadimpex.com"))
            self.assertEqual(response.content, b"served")
