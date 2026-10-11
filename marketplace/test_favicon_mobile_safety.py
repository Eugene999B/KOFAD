"""Google image crawl, safe mobile approvals and readable inventory controls.

The same official KOFAD icon must be served on both public hostnames.
This does not assert Google will instantly update its own search cache.
"""
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.domain_middleware import OfficialDomainMiddleware
from marketplace.seo import favicon_png, robots


@override_settings(ALLOWED_HOSTS=[
    "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com",
])
class PublicFaviconTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_public_market_favicon_is_real_96_pixel_png(self):
        from PIL import Image
        image_path = Path(settings.BASE_DIR) / "static" / "brand" / "favicon-96.png"
        if not image_path.exists():
            from scripts.prepare_logo import prepare_logo
            prepare_logo()
        with Image.open(image_path) as image:
            self.assertEqual(image.size, (96, 96))
            self.assertEqual(image.format, "PNG")
        for host in ("market.kofadimpex.com", "kofadimpex.com"):
            request = self.factory.get("/favicon-96.png", HTTP_HOST=host)
            intercepted = OfficialDomainMiddleware(lambda req: favicon_png(req))(request)
            self.assertEqual(intercepted.status_code, 200)
            self.assertEqual(intercepted["Content-Type"], "image/png")
            self.assertNotIn("X-Robots-Tag", intercepted)
            intercepted.close()

    def test_market_robots_explicitly_allows_high_resolution_icon(self):
        request = self.factory.get("/robots.txt", HTTP_HOST="market.kofadimpex.com")
        text = robots(request).content.decode()
        self.assertIn("Allow: /favicon-96.png", text)
        self.assertIn("Allow: /favicon.ico", text)
        self.assertIn("Allow: /market/", text)

    def test_new_icon_is_unambiguous_in_public_page_header(self):
        root = Path(settings.BASE_DIR)
        html = (root / "marketplace/templates/marketplace/base.html").read_text(encoding="utf-8")
        self.assertIn('rel="icon" type="image/png" sizes="96x96" href="/favicon-96.png"', html)
        self.assertIn('rel="shortcut icon" type="image/x-icon" href="/favicon.ico"', html)
        self.assertNotIn('rel="icon" type="image/x-icon" sizes="any"', html)


@override_settings(ALLOWED_HOSTS=[
    "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com",
])
class MarketIconHeadTests(TestCase):
    def test_market_index_advertises_same_origin_icon_and_is_indexable(self):
        response = self.client.get("/market/", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/favicon-96.png"')
        self.assertContains(response, 'name="robots" content="index,follow')


class MobileReadabilityTests(SimpleTestCase):
    def test_inventory_uses_labelled_cards_with_actions(self):
        root = Path(settings.BASE_DIR)
        template = (root / "templates/inventory.html").read_text(encoding="utf-8")
        js = (root / "static/responsive_tables.js").read_text(encoding="utf-8")
        css = (root / "static/kofad-mobile-reachability.css").read_text(encoding="utf-8")
        self.assertIn('data-mobile-table="cards"', template)
        self.assertIn('class="table-wrap inventory-stock-table"', template)
        self.assertIn("cell.dataset.mobileLabel", js)
        self.assertIn('table.dataset.mobileTable !== "cards"', js)
        self.assertIn("max-height:none!important", css)
        self.assertIn(".mobile-table-actions .row-actions", css)
        self.assertIn("position:sticky", css)
        self.assertEqual(css.count("{"), css.count("}"))

    def test_saved_approval_positions_are_clamped_to_dock_and_header(self):
        root = Path(settings.BASE_DIR)
        js = (root / "static/approval_attention.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelector(".mobile-dock")', js)
        self.assertIn('document.querySelector(".topbar")', js)
        self.assertIn("visualViewport", js)
        self.assertIn("keepReachable", js)
        self.assertIn("maxY", js)
        self.assertIn("applySavedPosition", js)
