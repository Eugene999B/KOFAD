"""Crawl safety regression: public sites indexed, private staff not exposed."""
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.http import HttpResponse

from core.domain_middleware import OfficialDomainMiddleware
from marketplace.seo import robots, sitemap


@override_settings(ALLOWED_HOSTS=[
    "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com",
])
class SearchCrawlTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, path, domain):
        return self.factory.get(path, secure=True, HTTP_HOST=domain)

    def test_company_robots_and_sitemap_are_public(self):
        rules = robots(self._request("/robots.txt", "kofadimpex.com")).content.decode()
        self.assertIn("Allow: /", rules)
        self.assertIn("https://kofadimpex.com/sitemap.xml", rules)
        xml = sitemap(self._request("/sitemap.xml", "kofadimpex.com")).content.decode()
        self.assertIn("<loc>https://kofadimpex.com/about/</loc>", xml)
        self.assertNotIn("/login/", xml)

    @patch("marketplace.seo.MarketListing.objects.filter")
    def test_market_sitemap_exposes_only_listed_product_ids(self, queryset):
        queryset.return_value.order_by.return_value.values_list.return_value.iterator.return_value = iter([21, 45])
        xml = sitemap(self._request("/sitemap.xml", "market.kofadimpex.com")).content.decode()
        self.assertIn("https://market.kofadimpex.com/market/products/21/", xml)
        self.assertIn("https://market.kofadimpex.com/market/products/45/", xml)
        self.assertNotIn("/market/account/", xml)
        queryset.assert_called_once_with(enabled=True, product__active=True)

    def test_staff_crawl_is_disallowed_and_responds_noindex(self):
        request = self._request("/workspace/", "staff.kofadimpex.com")
        self.assertIn("Disallow: /", robots(self._request("/robots.txt", "staff.kofadimpex.com")).content.decode())
        response = sitemap(self._request("/sitemap.xml", "staff.kofadimpex.com"))
        self.assertEqual(response.status_code, 404)
        response = OfficialDomainMiddleware(lambda _: HttpResponse("private"))(request)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow, noarchive")
