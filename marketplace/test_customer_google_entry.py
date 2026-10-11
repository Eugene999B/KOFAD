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

    def test_customer_auth_pages_are_single_card_without_old_blue_story(self):
        for path in ("/market/access/", "/market/account/login/"):
            with self.subTest(path=path):
                response = self.client.get(path, HTTP_HOST="market.kofadimpex.com")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'class="kofad-entry-shell"')
                self.assertContains(response, "premium-access-card")
                self.assertNotContains(response, 'class="kofad-entry-story"')
                self.assertNotContains(response, "Everything you need.")
                self.assertNotContains(response, "One easy account.")
                self.assertNotContains(response, "Shop with confidence, follow your orders")
                self.assertNotContains(response, "Mobile number linking is optional")
                self.assertContains(response, 'method="post"')
                self.assertContains(response, "Back to KOFAD Market")

    def test_customer_auth_styles_use_centered_single_column_card(self):
        from pathlib import Path
        from django.contrib.staticfiles import finders

        path = finders.find("marketplace/customer-auth-premium.css")
        self.assertIsNotNone(path)
        css = Path(path).read_text(encoding="utf-8")
        self.assertIn(".market-body .kofad-entry-shell{display:block;", css)
        self.assertIn("max-width:640px", css)
        self.assertNotIn("grid-template-columns:minmax(0,.88fr)", css)
        self.assertNotIn(".kofad-entry-story", css)

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
