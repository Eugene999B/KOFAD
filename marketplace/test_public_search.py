from django.conf import settings
from django.test import TestCase, override_settings


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
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("User-agent: *", body)
        self.assertIn("Sitemap: https://kofadimpex.com/sitemap.xml", body)
        self.assertIn("Disallow: /market/account/", body)
        self.assertIn("Disallow: /technical-admin/", body)

    @override_settings(STAFF_LOGIN_SLUG="private-staff-test-gateway")
    def test_robots_does_not_publish_configured_staff_gateway(self):
        response = self.client.get("/robots.txt")
        self.assertNotContains(response, "private-staff-test-gateway")

    def test_sitemap_contains_only_public_company_routes(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for path in ("/", "/market/", "/about/", "/contact/", "/faq/", "/delivery/"):
            self.assertIn(f"<loc>https://kofadimpex.com{path}</loc>", body)
        self.assertNotIn("/workspace/", body)
        self.assertNotIn("/market/account/", body)
        self.assertNotIn(settings.LOGIN_URL, body)
