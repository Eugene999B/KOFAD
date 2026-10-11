"""Native-browser installation must not cache or leak transactional data."""
from django.test import TestCase, override_settings
from core.tests import Fixtures


@override_settings(ALLOWED_HOSTS=[
    "localhost", "127.0.0.1", "testserver",
    "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com",
])
class InstallableWebAppTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def test_customer_manifest_is_installable_on_correct_origin(self):
        response = self.client.get(
            "/market/app/manifest.webmanifest",
            HTTP_HOST="market.kofadimpex.com", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/manifest+json"))
        manifest = response.json()
        self.assertEqual(manifest["name"], "KOFAD Market")
        self.assertEqual(manifest["id"], "/market/")
        self.assertEqual(manifest["scope"], "/market/")
        self.assertEqual(manifest["start_url"], "/market/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual({i["sizes"] for i in manifest["icons"]}, {"192x192", "512x512"})
        self.assertTrue(all(i["type"] == "image/png" for i in manifest["icons"]))
        self.assertNotIn("staff", str(manifest).lower())

    def test_staff_manifest_has_separate_start_scope_and_does_not_expose_staff_data(self):
        response = self.client.get(
            "/staff/app/manifest.webmanifest",
            HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["name"], "KOFAD Staff")
        self.assertEqual(data["start_url"], "/workspace/")
        self.assertEqual(data["scope"], "/")
        self.assertNotIn("customer", str(data).lower())
        self.assertIn("noindex", response["X-Robots-Tag"])

    def test_service_workers_never_cache_private_pages_or_payment_requests(self):
        for kind, host, script, scope, offline in (
            ("customer", "market.kofadimpex.com", "/market/app/sw.js", "/market/", "/market/app/offline/"),
            ("staff", "staff.kofadimpex.com", "/staff/app/sw.js", "/", "/staff/app/offline/"),
        ):
            with self.subTest(kind=kind):
                response = self.client.get(script, HTTP_HOST=host, secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Service-Worker-Allowed"], scope)
                self.assertIn("javascript", response["Content-Type"])
                self.assertIn("no-store", response["Cache-Control"])
                js = response.content.decode()
                self.assertIn(offline, js)
                self.assertIn('request.method !== "GET"', js)
                self.assertIn('request.mode !== "navigate"', js)
                self.assertIn("fetch(request).catch(", js)
                self.assertNotIn("cache.put(", js)
                self.assertNotIn("localStorage", js)
                self.assertNotIn("payment", js.lower())
                self.assertIn('type === "SKIP_WAITING"', js)
                off = self.client.get(offline, HTTP_HOST=host, secure=True)
                self.assertEqual(off.status_code, 200)
                self.assertContains(off, "Internet connection needed")
                self.assertNotContains(off, "password")
                self.assertIn("noindex", off["X-Robots-Tag"])

    def test_both_manifest_and_worker_use_the_canonical_app_origins(self):
        wrong_host = self.client.get(
            "/market/app/manifest.webmanifest",
            HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertEqual(wrong_host.status_code, 302)
        self.assertEqual(wrong_host["Location"],
                         "https://market.kofadimpex.com/market/app/manifest.webmanifest")
        wrong_staff_host = self.client.get(
            "/staff/app/sw.js", HTTP_HOST="kofadimpex.com", secure=True)
        self.assertEqual(wrong_staff_host.status_code, 302)
        self.assertEqual(wrong_staff_host["Location"], "https://staff.kofadimpex.com/staff/app/sw.js")

    def test_public_install_pages_are_not_misleading_about_native_installers(self):
        install = self.client.get("/market/app/install/")
        self.assertEqual(install.status_code, 200)
        self.assertContains(install, "Use KOFAD on iPhone.")
        self.assertContains(install, "Show iPhone steps")
        self.assertContains(install, "Add to Home Screen")
        self.assertContains(install, 'data-pwa-guide="iphone"')
        self.assertContains(install, 'rel="manifest"')
        self.assertNotContains(install, "Install KOFAD Staff")
        public = self.client.get("/apps/")
        self.assertContains(public, "Add as web app")
        self.assertContains(public, "Windows")
        home = self.client.get("/")
        self.assertContains(home, "Download the app")

    def test_private_staff_download_page_offers_browser_install_only_after_signin(self):
        self.assertEqual(self.client.get("/staff/app/").status_code, 302)
        self.authenticate_client()
        r = self.client.get("/staff/app/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "KOFAD for your workday.")
        self.assertContains(r, 'data-pwa-install')
        self.assertContains(r, 'rel="manifest"')
        self.assertIn("no-store", r["Cache-Control"])
