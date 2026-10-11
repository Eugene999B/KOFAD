"""Regression tests for KOFAD's self-hosted homepage photo and Phase 3 surfaces."""
from pathlib import Path
from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings


class KofadHeroAssetTests(SimpleTestCase):
    def test_first_slide_is_replaced_with_large_bundled_image(self):
        root = Path(settings.BASE_DIR)
        photo = root / "marketplace/static/marketplace/kofad-home-hero-sharp.webp"
        self.assertTrue(photo.is_file(), "Large replacement image must be committed to GitHub")
        self.assertGreater(photo.stat().st_size, 100_000, "Reject the old blurry tiny image")
        image = photo.read_bytes()
        self.assertEqual(image[:4], b"RIFF")
        self.assertEqual(image[8:12], b"WEBP")
        html = (root / "marketplace/templates/marketplace/home.html").read_text()
        js = (root / "marketplace/static/marketplace/home-refresh.js").read_text()
        self.assertIn("kofad-home-hero-sharp.webp", html)
        self.assertIn("kofad-home-hero-sharp.webp", js)
        self.assertNotIn("kofad-market-retail-hero.webp", html)
        self.assertNotIn("kofad-market-retail-hero.webp", js)
        self.assertEqual(js.count('"https://images.unsplash.com/photo-'), 5)
        self.assertIn('data-hero-count', html)
        self.assertIn("01 / 06", html)
        self.assertIn('id="kfd-hero-title"', html)
        self.assertNotIn("data-slide-pause", html)
        self.assertNotIn("data-slide-pause", js)
        self.assertIn("setInterval", js)
        self.assertIn("8500", js)
        self.assertIn("prefers-reduced-motion", js)

    def test_phase_three_css_scoped_and_responsive(self):
        root = Path(settings.BASE_DIR)
        for shell in ("templates/base.html", "marketplace/templates/marketplace/base.html"):
            markup = (root / shell).read_text()
            self.assertIn("kofad-atelier-commerce.css", markup)
        css = (root / "static/kofad-atelier-commerce.css").read_text()
        for name in ("#pos", ".finance-workspace", ".online-order-hero",
                     ".commerce-checkout-form", ".order-history-card",
                     'html[data-theme="dark"]', "prefers-reduced-motion"):
            self.assertIn(name, css)
        self.assertEqual(css.count("{"), css.count("}"))


@override_settings(ALLOWED_HOSTS=["kofadimpex.com", "market.kofadimpex.com",
                                  "staff.kofadimpex.com"])
class KofadHeroPublicTests(TestCase):
    def test_homepage_uses_large_bundled_first_photo(self):
        response = self.client.get("/", HTTP_HOST="kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kofad-home-hero-sharp")
        self.assertContains(response, "01 / 06")
        self.assertContains(response, "kofad-atelier-commerce")

    def test_market_still_shows_actual_catalogue(self):
        response = self.client.get("/market/", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "kofad-atelier-commerce")
        self.assertContains(response, "All products")
