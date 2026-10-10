"""Security, accuracy and boundedness of the public mobile catalog."""
from django.test import TestCase, override_settings

from core.tests import Fixtures
from marketplace.models import MarketListing


@override_settings(ALLOWED_HOSTS=[
    "localhost", "127.0.0.1", "testserver",
    "kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com",
])
class NativeCatalogTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.listing = MarketListing.objects.create(
            product=self.product, enabled=True, featured=True, title="Test carton",
            price_source="retail_unit",
        )

    def test_public_feed_contains_only_allowed_product_information(self):
        response = self.client.get(
            "/market/app/catalog.json",
            HTTP_HOST="market.kofadimpex.com",
            HTTP_ORIGIN="capacitor://localhost",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "capacitor://localhost")
        self.assertEqual(response["Access-Control-Allow-Methods"], "GET")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        data = response.json()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(len(data["items"]), 1)
        product = data["items"][0]
        self.assertEqual(product["id"], self.listing.pk)
        self.assertEqual(product["name"], "Test carton")
        self.assertEqual(product["price"], "50.00")
        self.assertEqual(product["currency"], "GHS")
        self.assertTrue(product["in_stock_snapshot"])
        self.assertEqual(product["product_path"], f"/market/products/{self.listing.pk}/")
        self.assertFalse({"phone", "customer", "branch", "secret", "cost", "password"} & product.keys())

    def test_native_description_is_plain_text_and_bounded(self):
        self.listing.description = "<b>Kitchen</b> essentials and &amp; supplies " + "x" * 900
        self.listing.save(update_fields=["description"])
        response = self.client.get("/market/app/catalog.json")
        description = response.json()["items"][0]["description"]
        self.assertIn("Kitchen", description)
        self.assertNotIn("<b>", description)
        self.assertLessEqual(len(description), 500)

    def test_malicious_website_cannot_receive_native_cors_permission(self):
        for origin in (
            "https://malicious.example", "https://localhost.evil.example",
            "http://localhost:9000", "null",
        ):
            response = self.client.get("/market/app/catalog.json", HTTP_ORIGIN=origin)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("Access-Control-Allow-Origin", response)
        for origin in ("http://localhost", "https://localhost"):
            response = self.client.get("/market/app/catalog.json", HTTP_ORIGIN=origin)
            self.assertEqual(response["Access-Control-Allow-Origin"], origin)

    def test_disabled_or_inactive_listings_not_exposed(self):
        self.listing.enabled = False
        self.listing.save(update_fields=["enabled"])
        response = self.client.get("/market/app/catalog.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_catalog_is_read_only_and_search_is_bounded(self):
        response = self.client.get("/market/app/catalog.json", {"q": "not there", "page": "999999999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])
        self.assertEqual(response.json()["page"], 100)
        self.assertEqual(self.client.post("/market/app/catalog.json").status_code, 405)

    def test_staff_host_redirects_to_customer_market_origin(self):
        response = self.client.get("/market/app/catalog.json", HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://market.kofadimpex.com/market/app/catalog.json")
