"""Regression tests for staff sign-in recovery after missing OAuth URL names."""
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import resolve, reverse


@override_settings(ALLOWED_HOSTS=[
    "localhost", "127.0.0.1", "testserver",
    "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com",
])
@override_settings(ALLOWED_HOSTS=["testserver", "staff.kofadimpex.com"])
class StaffLoginRouteRegressionTests(TestCase):
    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=True,
        KOFAD_GOOGLE_CLIENT_ID="test-id.apps.googleusercontent.com",
        KOFAD_GOOGLE_CLIENT_SECRET="test-only-client-secret",
    )
    def test_staff_login_renders_with_google_enabled_instead_of_http_500(self):
        response = self.client.get("/login/", HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in with Google")
        self.assertContains(response, reverse("google_staff_login"))

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=False,
        KOFAD_GOOGLE_CLIENT_ID="",
        KOFAD_GOOGLE_CLIENT_SECRET="",
    )
    def test_staff_login_renders_without_google_enabled(self):
        response = self.client.get("/login/", HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Sign in with Google")

    def test_configured_private_login_path_is_registered(self):
        path = settings.STAFF_LOGIN_PATH
        self.assertEqual(resolve(path).url_name, "staff_private_login")
        response = self.client.get(path, HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(response.status_code, 200)

    def test_staff_and_customer_google_auth_paths_are_named(self):
        self.assertEqual(reverse("google_staff_login"), "/auth/google/staff/login/")
        self.assertEqual(reverse("google_staff_callback"), "/auth/google/staff/callback/")
        self.assertEqual(reverse("google_staff_link"), "/auth/google/staff/link/")
        self.assertEqual(reverse("google_customer_login"), "/market/auth/google/login/")
        self.assertEqual(reverse("google_customer_callback"), "/market/auth/google/callback/")
        self.assertEqual(reverse("market_google_finish"), "/market/auth/google/finish/")
