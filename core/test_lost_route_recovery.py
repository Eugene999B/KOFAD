"""Regression contract for KOFAD routes lost by 2026-10-10 merge.

Check original route names, resolved view availability, security boundaries and
vital owner paths. Never call payment charge or message-send endpoints.
"""
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import resolve, reverse

from core.models import Branch, Company


RESTORED = {
    "email_center": "/email/",
    "email_work": "/email/work/",
    "email_drafts": "/email/drafts/",
    "email_replies": "/email/replies/",
    "email_reports": "/email/reports/",
    "email_history": "/email/history/",
    "email_automations": "/email/automations/",
    "email_team": "/email/team/",
    "email_campaigns": "/email/campaigns/",
    "email_ingest": "/email/ingest/",
    "email_brevo_events": "/email/events/brevo/",
    "gmail_sender_connect": "/auth/google/gmail/connect/",
    "gmail_sender_callback": "/auth/google/gmail/callback/",
    "gmail_sender_disconnect": "/auth/google/gmail/disconnect/",
    "gmail_sender_test": "/auth/google/gmail/send-test/",
    "customer_care_settings": "/settings/customer-care/",
    "customer_duplicate_check": "/api/customers/check-duplicate/",
    "staff_invitation_complete": "/staff-invite/complete/",
    "staff_invite_resend": "/administration/users/22/invite/resend/",
    "staff_invitation_open": "/staff-invite/22/sample-invite/",
    "pos_momo_payments": "/payments/momo/",
    "pos_momo_payment": "/payments/momo/example/",
    "pos_momo_verify": "/payments/momo/example/verify/",
    "staff_native_downloads": "/staff/app/",
    "mobile_operations_dashboard": "/staff/app/control/",
    "staff_native_releases": "/staff/app/releases.json",
    "staff_native_version": "/staff/app/native-version.json",
    "staff_pwa_manifest": "/staff/app/manifest.webmanifest",
    "staff_pwa_sw": "/staff/app/sw.js",
    "staff_pwa_offline": "/staff/app/offline/",
    "staff_mobile_bootstrap": "/staff/mobile/v1/bootstrap/",
    "staff_mobile_capabilities": "/staff/mobile/v1/capabilities/",
    "staff_mobile_authorize": "/staff/mobile/v1/authorize/",
    "staff_mobile_token": "/staff/mobile/v1/token/",
    "staff_mobile_me": "/staff/mobile/v1/me/",
    "staff_mobile_revoke": "/staff/mobile/v1/revoke/",
    "customer_native_downloads": "/apps/",
    "customer_native_releases": "/apps/releases.json",
    "market_native_releases": "/market/app/releases.json",
    "market_native_version": "/market/app/native-version.json",
    "market_native_catalog": "/market/app/catalog.json",
    "market_mobile_bootstrap": "/market/mobile/v1/bootstrap/",
    "market_mobile_capabilities": "/market/mobile/v1/capabilities/",
    "market_mobile_authorize": "/market/mobile/v1/authorize/",
    "market_mobile_token": "/market/mobile/v1/token/",
    "market_mobile_me": "/market/mobile/v1/me/",
    "market_mobile_revoke": "/market/mobile/v1/revoke/",
    "market_mobile_product_detail": "/market/mobile/v1/products/22/",
    "market_pwa_manifest": "/market/app/manifest.webmanifest",
    "market_pwa_sw": "/market/app/sw.js",
    "market_pwa_offline": "/market/app/offline/",
    "market_pwa_install": "/market/app/install/",
    "robots_txt": "/robots.txt",
    "sitemap_xml": "/sitemap.xml",
    "site_favicon": "/favicon.ico",
    "site_favicon_png": "/favicon-96.png",
    "public_wholesale": "/wholesale/",
    "market_category": "/market/categories/cocoa/",
}


class LostRouteContractTests(SimpleTestCase):
    def test_all_restored_route_names_resolve_to_original_paths(self):
        for name, path in RESTORED.items():
            with self.subTest(name=name):
                self.assertEqual(resolve(path).url_name, name)
                self.assertEqual(reverse(name, args=resolve(path).args,
                                         kwargs=resolve(path).kwargs), path)

    def test_existing_critical_url_names_not_broken_by_restore(self):
        for name in (
            "dashboard", "settings", "connections_center",
            "google_staff_login", "google_customer_login",
            "market_login", "market_access", "market_checkout",
            "google_weekly_backup_export",
        ):
            with self.subTest(name=name):
                self.assertTrue(reverse(name).startswith("/"))


class LostRouteOwnerSmokeTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("routes-owner", password="ci-synthetic-password")
        self.branch = Branch.objects.create(name="Route recovery", code="routerec")
        Company.objects.create()
        self.owner.access.refresh_from_db()
        self.client.force_login(self.owner)
        session = self.client.session
        session["access_version"] = self.owner.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    @override_settings(KOFAD_EMAIL_CENTER_ENABLED=True)
    def test_read_only_owner_pages_are_reachable(self):
        for url in ("/workspace/", "/settings/", "/settings/connections/",
                    "/email/", "/email/work/", "/email/drafts/", "/email/replies/",
                    "/email/reports/", "/email/campaigns/", "/settings/customer-care/",
                    "/payments/momo/", "/staff/app/", "/staff/app/control/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertNotEqual(response.status_code, 404)
                self.assertNotEqual(response.status_code, 500)

    def test_anonymous_can_not_open_private_routes(self):
        self.client.logout()
        for url in ("/email/", "/email/drafts/", "/payments/momo/",
                    "/staff/app/", "/staff/app/control/",
                    "/settings/connections/"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertIn(response.status_code, (302, 403, 404))

    def test_public_index_and_app_metadata_do_not_crash(self):
        for url in ("/robots.txt", "/sitemap.xml", "/wholesale/",
                    "/market/app/manifest.webmanifest",
                    "/apps/releases.json", "/market/app/catalog.json"):
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_HOST="market.kofadimpex.com")
                self.assertNotEqual(response.status_code, 500)
                self.assertNotEqual(response.status_code, 404)

    def test_nonexistent_market_category_correctly_returns_404(self):
        response = self.client.get("/market/categories/nonexistent/", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 404)
