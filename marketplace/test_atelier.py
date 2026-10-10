"""Visual roll-out guardrails: shared theme, public storefront and staff navigation."""
from pathlib import Path

from django.conf import settings
from django.test import TestCase, SimpleTestCase, override_settings


@override_settings(ALLOWED_HOSTS=["kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com"])
class AtelierPublicTests(TestCase):
    def test_homepage_uses_brand_system_and_real_journeys(self):
        response = self.client.get("/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kofad-atelier.css")
        self.assertContains(response, "atelier-home-story")
        self.assertContains(response, "Find your products")
        self.assertContains(response, "Discuss bulk supply")
        self.assertContains(response, "Plan collection or delivery")
        self.assertContains(response, 'href="/market/"')

    def test_market_uses_live_catalogue_and_keeps_checkout_paths(self):
        response = self.client.get("/market/", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="atelier-market-intro"')
        self.assertContains(response, "kofad-atelier.css")
        self.assertContains(response, "<h1>All products</h1>", html=True)
        self.assertContains(response, 'href="/wholesale/"')

    def test_customer_help_remains_available_without_login(self):
        response = self.client.get("/contact/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kofad-atelier.css")


class AtelierAssetsTests(SimpleTestCase):
    def test_unified_theme_defines_light_dark_and_responsive_rules(self):
        file = Path(settings.BASE_DIR) / "static" / "kofad-atelier.css"
        css = file.read_text(encoding="utf-8")
        self.assertIn('html[data-theme="dark"]', css)
        self.assertIn('body[data-session-zone="staff"]', css)
        self.assertIn("atelier-market-intro", css)
        self.assertIn("atelier-home-story", css)
        self.assertIn("prefers-reduced-motion:reduce", css)
        self.assertNotIn("https://", css)
