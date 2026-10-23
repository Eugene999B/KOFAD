"""Visual roll-out guardrails: shared theme, public storefront and staff navigation."""
from pathlib import Path

from django.conf import settings
from django.test import TestCase, SimpleTestCase, override_settings


@override_settings(ALLOWED_HOSTS=["kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com"])
class AtelierPublicTests(TestCase):
    def test_homepage_uses_brand_system_and_real_journeys(self):
        response = self.client.get("/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kofad-atelier")
        self.assertContains(response, "atelier-home-story")
        self.assertContains(response, "Have a look")
        self.assertContains(response, "Ask about bulk orders")
        self.assertContains(response, "Choose pickup or delivery")
        self.assertContains(response, 'href="/market/"')

    def test_market_uses_live_catalogue_and_keeps_checkout_paths(self):
        response = self.client.get("/market/", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="market-shop-lead"')
        self.assertContains(response, "market-discover")
        self.assertNotContains(response, 'class="atelier-market-intro"')
        self.assertNotContains(response, 'class="market-guest-welcome"')
        self.assertContains(response, "kofad-atelier")
        self.assertContains(response, "<h1>All products</h1>", html=True)
        self.assertContains(response, 'href="/wholesale/"')

    def test_customer_help_remains_available_without_login(self):
        response = self.client.get("/contact/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kofad-atelier")


class AtelierAssetsTests(SimpleTestCase):
    def test_catalogue_product_card_has_no_forms_inside_links(self):
        """Wishlist is an independent action, never nested in a product link."""
        from html.parser import HTMLParser

        class CardHTMLInspector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.link_depth = 0
                self.nested_forms = []

            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.link_depth += 1
                elif tag == "form" and self.link_depth:
                    self.nested_forms.append(attrs)

            def handle_endtag(self, tag):
                if tag == "a":
                    self.link_depth = max(0, self.link_depth - 1)

        path = Path(settings.BASE_DIR) / "marketplace/templates/marketplace/product_card.html"
        inspector = CardHTMLInspector()
        inspector.feed(path.read_text(encoding="utf-8"))
        self.assertEqual(inspector.nested_forms, [], "Product-card forms must not be nested inside links.")

    def test_product_cards_use_compact_accessible_actions(self):
        root = Path(settings.BASE_DIR)
        card = (root / "marketplace/templates/marketplace/product_card.html").read_text()
        stylesheet = (root / "marketplace/static/marketplace/product-actions.css").read_text()
        base = (root / "marketplace/templates/marketplace/base.html").read_text()
        self.assertIn("product-action-details", card)
        self.assertIn("product-action-add", card)
        self.assertIn("product-action-icon", card)
        self.assertIn("View details", card)
        self.assertIn("Add to cart", card)
        self.assertIn('aria-label="View details of', card)
        self.assertIn('aria-label="Sign in to add', card)
        self.assertIn("width:16px !important", stylesheet)
        self.assertIn("height:16px !important", stylesheet)
        self.assertIn("min-height:44px !important", stylesheet)
        self.assertIn("prefers-reduced-motion", stylesheet)
        self.assertIn("marketplace/product-actions.css", base)

    def test_unified_theme_defines_light_dark_and_responsive_rules(self):
        file = Path(settings.BASE_DIR) / "static" / "kofad-atelier.css"
        css = file.read_text(encoding="utf-8")
        self.assertIn('html[data-theme="dark"]', css)
        self.assertIn('body[data-session-zone="staff"]', css)
        self.assertIn("atelier-market-intro", css)
        self.assertIn("atelier-home-story", css)
        self.assertIn("prefers-reduced-motion:reduce", css)
        self.assertNotIn("https://", css)
