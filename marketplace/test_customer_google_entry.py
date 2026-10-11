"""Guard Google account-access buttons on every KOFAD customer entry page."""
from django.test import override_settings
from django.urls import reverse

from .tests import MarketFixtures


@override_settings(ALLOWED_HOSTS=["testserver", "market.kofadimpex.com", "staff.kofadimpex.com"])
class CustomerGoogleEntryPointTests(MarketFixtures):
    def _check_pages(self, *, displayed):
        for path in ("/market/access/", "/market/account/login/"):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST="market.kofadimpex.com")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["google_ready"], displayed)
                self.assertEqual(
                    "Continue with Google" in response.content.decode("utf-8"),
                    displayed,
                )
                if displayed:
                    self.assertContains(response, reverse("google_customer_login"))
                else:
                    self.assertNotContains(response, 'href="/market/auth/google/login/"')

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=True,
        KOFAD_GOOGLE_CLIENT_ID="test-customer.apps.googleusercontent.com",
        KOFAD_GOOGLE_CLIENT_SECRET="dummy-test-secret-not-a-live-credential",
    )
    def test_google_enabled_shows_button_on_gateway_and_signin(self):
        self._check_pages(displayed=True)

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=False,
        KOFAD_GOOGLE_CLIENT_ID="test-customer.apps.googleusercontent.com",
        KOFAD_GOOGLE_CLIENT_SECRET="dummy-test-secret-not-a-live-credential",
    )
    def test_google_disabled_hides_button_on_both_pages(self):
        self._check_pages(displayed=False)

    @override_settings(
        KOFAD_GOOGLE_OAUTH_ENABLED=True,
        KOFAD_GOOGLE_CLIENT_ID="",
        KOFAD_GOOGLE_CLIENT_SECRET="",
    )
    def test_incomplete_google_credentials_do_not_show_dead_link(self):
        self._check_pages(displayed=False)
