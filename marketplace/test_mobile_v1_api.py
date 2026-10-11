"""Contract tests for the versioned KOFAD public mobile Market endpoints."""
from django.test import TestCase, override_settings
from core.tests import Fixtures
from marketplace.models import MarketListing


@override_settings(ALLOWED_HOSTS=[
    "localhost", "testserver", "market.kofadimpex.com", "staff.kofadimpex.com",
])
class MarketMobileV1Tests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.listing = MarketListing.objects.create(
            product=self.product, enabled=True, featured=True,
            title="Test carton", price_source="retail_unit",
            description="<p><strong>Trusted</strong> household essentials</p>",
            tags="Home, Wholesale",
            highlights=["Official KOFAD range", "Available while stocks last"],
        )

    def test_public_bootstrap_is_truthful_about_missing_mobile_auth(self):
        response = self.client.get(
            "/market/mobile/v1/bootstrap/",
            HTTP_HOST="market.kofadimpex.com",
            HTTP_ORIGIN="https://localhost",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://localhost")
        data = response.json()
        self.assertEqual(data["version"], 1)
        self.assertTrue(data["features"]["guest_catalog"])
        self.assertFalse(data["features"]["mobile_checkout"])
        self.assertFalse(data["features"]["mobile_account_session"])
        self.assertFalse(data["features"]["background_push"])
        self.assertNotIn("staff", str(data).lower())
        self.assertEqual(self.client.post("/market/mobile/v1/bootstrap/").status_code, 405)

    def test_public_product_detail_is_consistent_and_public_only(self):
        response = self.client.get(
            f"/market/mobile/v1/products/{self.listing.pk}/",
            HTTP_HOST="market.kofadimpex.com",
            HTTP_ORIGIN="capacitor://localhost",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "capacitor://localhost")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        p = response.json()["product"]
        self.assertEqual(p["id"], self.listing.pk)
        self.assertEqual(p["name"], "Test carton")
        self.assertEqual(p["price"], "50.00")
        self.assertEqual(p["currency"], "GHS")
        self.assertIn("Trusted household", p["description"])
        self.assertNotIn("<", p["description"])
        self.assertTrue(p["in_stock_snapshot"])
        self.assertEqual(p["tags"], ["Home", "Wholesale"])
        self.assertEqual(p["gallery"], [])
        self.assertFalse(
            {"cost", "stock_quantity", "supplier", "secret", "customer", "phone", "branch"} & p.keys()
        )

    def test_inactive_listings_cannot_be_read(self):
        self.listing.enabled = False
        self.listing.save(update_fields=["enabled"])
        self.assertEqual(self.client.get(f"/market/mobile/v1/products/{self.listing.pk}/").status_code, 404)
        self.listing.enabled = True
        self.listing.save(update_fields=["enabled"])
        self.product.active = False
        self.product.save(update_fields=["active"])
        self.assertEqual(self.client.get(f"/market/mobile/v1/products/{self.listing.pk}/").status_code, 404)

    def test_untrusted_website_cannot_get_native_origin_cors(self):
        response = self.client.get(
            f"/market/mobile/v1/products/{self.listing.pk}/",
            HTTP_ORIGIN="https://malicious.example",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)
        self.assertEqual(self.client.post(
            f"/market/mobile/v1/products/{self.listing.pk}/"
        ).status_code, 405)
