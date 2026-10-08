from django.conf import settings
from django.test import TestCase, override_settings


@override_settings(ALLOWED_HOSTS=["testserver", "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com"])
class PublicSearchSurfaceTests(TestCase):
    def test_home_is_indexable_and_has_no_staff_login_link(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("X-Robots-Tag", response)
        self.assertContains(response, 'name="robots" content="index,follow,max-image-preview:large"')
        self.assertContains(response, 'rel="canonical" href="https://kofadimpex.com/"')
        self.assertNotContains(response, "Staff login")
        self.assertNotContains(response, "Staff access")
        self.assertNotContains(response, 'href="/login/"')
        self.assertContains(response, "brand/kofad-official-logo")

    def test_staff_and_customer_private_surfaces_are_noindex(self):
        staff = self.client.get(settings.LOGIN_URL)
        self.assertEqual(staff.status_code, 200)
        self.assertEqual(staff["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertContains(staff, 'name="robots" content="noindex,nofollow,noarchive"')

        customer = self.client.get("/market/access/")
        self.assertEqual(customer.status_code, 200)
        self.assertEqual(customer["X-Robots-Tag"], "noindex, nofollow, noarchive")

    def test_robots_exposes_sitemap_without_revealing_private_staff_slug(self):
        response = self.client.get("/robots.txt", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("User-agent: *", body)
        self.assertIn("Sitemap: https://kofadimpex.com/sitemap.xml", body)
        self.assertIn("Disallow: /technical-admin/", body)
        self.assertNotIn(settings.STAFF_LOGIN_SLUG, body)

        staff = self.client.get("/robots.txt", HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(staff.status_code, 200)
        self.assertIn("Disallow: /", staff.content.decode())
        self.assertNotIn(settings.STAFF_LOGIN_SLUG, staff.content.decode())

        market = self.client.get("/robots.txt", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(market.status_code, 200)
        market_body = market.content.decode()
        self.assertIn("Allow: /market/", market_body)
        self.assertIn("Disallow: /market/account/", market_body)

    @override_settings(STAFF_LOGIN_SLUG="private-staff-test-gateway")
    def test_robots_does_not_publish_configured_staff_gateway(self):
        response = self.client.get("/robots.txt", HTTP_HOST="kofadimpex.com")
        self.assertNotContains(response, "private-staff-test-gateway")

    def test_market_uses_its_real_host_as_canonical(self):
        response = self.client.get("/market/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'rel="canonical" href="https://market.kofadimpex.com/market/"',
        )

    def test_sitemap_contains_only_public_company_routes(self):
        response = self.client.get("/sitemap.xml", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for path in ("/", "/about/", "/contact/", "/faq/", "/delivery/"):
            self.assertIn(f"<loc>https://kofadimpex.com{path}</loc>", body)
        self.assertNotIn("https://market.kofadimpex.com/", body)
        market = self.client.get("/sitemap.xml", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(market.status_code, 200)
        self.assertIn("<loc>https://market.kofadimpex.com/market/</loc>", market.content.decode())
        self.assertNotIn("https://kofadimpex.com/about/", market.content.decode())
        self.assertNotIn("/workspace/", body)
        self.assertNotIn("/market/account/", body)
        self.assertNotIn(settings.LOGIN_URL, body)
