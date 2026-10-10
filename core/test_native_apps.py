"""Native app distribution must not cross customer/staff security boundaries."""
from django.test import TestCase, override_settings

from core.native_apps import approved_release_url, app_metadata
from core.tests import Fixtures


@override_settings(ALLOWED_HOSTS=[
    "localhost", "127.0.0.1", "testserver",
    "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com",
])
class NativeAppDownloadsTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def test_customer_downloads_are_public_and_exclude_staff_installation_details(self):
        response = self.client.get("/apps/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "KOFAD Market")
        self.assertContains(response, "Android")
        self.assertContains(response, "Windows")
        self.assertNotContains(response, "KOFAD Staff")
        self.assertContains(response, "Our dedicated apps are being prepared")
        self.assertContains(response, "Not released yet", count=3)
        self.assertContains(response, "native-brands/android")
        self.assertContains(response, "native-brands/apple")
        self.assertContains(response, "native-brands/windows")
        self.assertContains(response, "Continue to Market")
        self.assertNotContains(response, "Download staff app")

    def test_public_and_market_have_customer_native_app_promotion(self):
        self.assertContains(self.client.get("/"), "Explore the upcoming app")
        self.assertContains(self.client.get("/market/"), "View app release status")
        self.assertNotContains(self.client.get("/"), "Download staff app")

    def test_staff_page_requires_valid_staff_session(self):
        response = self.client.get("/staff/app/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
        response = self.client.get("/staff/app/releases.json")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_staff_download_link_is_only_in_authenticated_workspace(self):
        self.authenticate_client()
        response = self.client.get("/staff/app/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "KOFAD Staff")
        self.assertContains(response, "Open workspace")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("noindex", response["X-Robots-Tag"])
        self.assertContains(self.client.get("/workspace/"), "Download staff app")

    @override_settings(
        KOFAD_CUSTOMER_APP_VERSION="1.2.3",
        KOFAD_CUSTOMER_APP_ANDROID_URL="https://play.google.com/store/apps/details?id=com.kofadimpex.market",
        KOFAD_CUSTOMER_APP_IOS_URL="https://apps.apple.com/gh/app/kofad-market/id1234567890",
        KOFAD_CUSTOMER_APP_WINDOWS_URL="https://downloads.kofadimpex.com/windows/customer/KOFAD-Market.exe",
        KOFAD_STAFF_APP_VERSION="2.0.1",
        KOFAD_STAFF_APP_WINDOWS_URL="https://downloads.kofadimpex.com/windows/staff/KOFAD-Staff.exe",
    )
    def test_independent_release_metadata_uses_only_approved_links(self):
        public = self.client.get("/market/app/releases.json", HTTP_HOST="market.kofadimpex.com", secure=True)
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["channel"], "customer")
        self.assertEqual(public.json()["version"], "1.2.3")
        self.assertTrue(public.json()["platforms"]["android"]["available"])
        self.assertNotIn("staff", str(public.content).lower())
        self.authenticate_client()
        staff = self.client.get("/staff/app/releases.json", HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertEqual(staff.status_code, 200)
        self.assertEqual(staff.json()["channel"], "staff")
        self.assertEqual(staff.json()["version"], "2.0.1")
        self.assertIn("no-store", staff["Cache-Control"])

    @override_settings(
        KOFAD_CUSTOMER_APP_ANDROID_URL="http://evil.example/android.apk",
        KOFAD_CUSTOMER_APP_IOS_URL="https://apps.apple.com.evil.example/store",
        KOFAD_CUSTOMER_APP_WINDOWS_URL="https://evil.example/unsigned.exe",
    )
    def test_fake_download_links_never_activate(self):
        response = self.client.get("/apps/")
        self.assertContains(response, "In preparation", count=3)
        self.assertNotContains(response, "Get Android app")
        self.assertFalse(app_metadata("customer")["released"])

    @override_settings(
        KOFAD_CUSTOMER_APP_VERSION="1.4.0",
        KOFAD_CUSTOMER_APP_ANDROID_URL="https://play.google.com/store/apps/details?id=com.kofadimpex.market",
    )
    def test_published_android_link_is_active_but_unreleased_ios_windows_remain_pending(self):
        page = self.client.get("/apps/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Get Android app")
        self.assertContains(page, "Ready to download", count=1)
        self.assertContains(page, "In preparation", count=2)
        self.assertNotContains(page, "Get Windows installer")
        self.assertNotContains(page, "View on the App Store")
        home = self.client.get("/")
        self.assertContains(home, "Get the KOFAD app")
        market = self.client.get("/market/")
        self.assertContains(market, "Explore app downloads")

    @override_settings(
        KOFAD_CUSTOMER_APP_VERSION="1.3.2",
        KOFAD_CUSTOMER_APP_IOS_URL="https://apps.apple.com/gh/app/kofad-market/id1234567890",
        KOFAD_STAFF_APP_VERSION="3.4.5",
        KOFAD_STAFF_APP_ANDROID_URL="https://play.google.com/store/apps/details?id=com.kofadimpex.staff",
    )
    def test_local_native_app_update_feed_is_public_but_contains_no_staff_urls(self):
        consumer = self.client.get(
            "/market/app/native-version.json",
            HTTP_HOST="market.kofadimpex.com",
            HTTP_ORIGIN="capacitor://localhost", secure=True,
        )
        self.assertEqual(consumer.status_code, 200)
        self.assertEqual(consumer["Access-Control-Allow-Origin"], "capacitor://localhost")
        self.assertEqual(consumer.json(), {
            "channel": "customer",
            "version": "1.3.2",
            "platforms": {"android": False, "ios": True, "windows": False},
        })
        staff = self.client.get(
            "/staff/app/native-version.json",
            HTTP_HOST="staff.kofadimpex.com",
            HTTP_ORIGIN="https://localhost", secure=True,
        )
        self.assertEqual(staff.status_code, 200)
        self.assertEqual(staff["Access-Control-Allow-Origin"], "https://localhost")
        self.assertEqual(staff.json(), {
            "channel": "staff",
            "version": "3.4.5",
            "platforms": {"android": True, "ios": False, "windows": False},
        })
        self.assertIn("noindex", staff["X-Robots-Tag"])
        self.assertNotIn("play.google.com", staff.content.decode())
        self.assertNotIn("apps.apple.com", consumer.content.decode())
        untrusted = self.client.get(
            "/staff/app/native-version.json",
            HTTP_HOST="staff.kofadimpex.com",
            HTTP_ORIGIN="https://evil.example", secure=True,
        )
        self.assertEqual(untrusted.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", untrusted)
        self.assertEqual(self.client.post("/staff/app/native-version.json").status_code, 405)

    def test_reject_fake_store_urls_redirectors_and_invalid_protocols(self):
        invalid = [
            ("http://downloads.kofadimpex.com/customer.apk", "android"),
            ("https://play.google.com.evil.example/store/apps/details?id=1", "android"),
            ("https://play.google.com/store/apps/details?id=1#spoof", "android"),
            ("https://staff.kofadimpex.com.evil.example/setup.exe", "windows"),
            ("file:///C:/kofad.exe", "windows"),
            ("https://apps.apple.com.evil.example/gh/app/123", "ios"),
            ("https://test:password@apps.apple.com/gh/app/123", "ios"),
        ]
        for url, platform in invalid:
            self.assertEqual(approved_release_url(url, platform), "", url)

    @override_settings(
        KOFAD_CUSTOMER_APP_ANDROID_URL="https://play.google.com/store/apps/details?id=com.kofadimpex.staff",
        KOFAD_CUSTOMER_APP_WINDOWS_URL="https://downloads.kofadimpex.com/windows/staff/setup.exe",
        KOFAD_STAFF_APP_ANDROID_URL="https://play.google.com/store/apps/details?id=com.kofadimpex.market",
        KOFAD_STAFF_APP_WINDOWS_URL="https://downloads.kofadimpex.com/windows/customer/setup.exe",
    )
    def test_customer_and_staff_release_addresses_cannot_be_mixed(self):
        public = app_metadata("customer")
        staff = app_metadata("staff")
        self.assertFalse(public["released"])
        self.assertFalse(staff["released"])
        self.assertTrue(all(not item["available"] for item in public["platforms"]))
        self.assertTrue(all(not item["available"] for item in staff["platforms"]))

    def test_market_host_redirects_public_app_link_to_canonical_home(self):
        response = self.client.get("/apps/", HTTP_HOST="market.kofadimpex.com", secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://kofadimpex.com/apps/")
