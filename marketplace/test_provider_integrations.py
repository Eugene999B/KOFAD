"""New cloud integrations must preserve KOFAD's database source of truth."""
from decimal import Decimal
from unittest.mock import Mock, patch

import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings

from core.models import Product
from marketplace.cloudinary_images import mirror_public_image
from marketplace.models import MarketListing
from marketplace.qdrant_products import (
    COLLECTION, MODEL, configured as qdrant_configured,
    semantic_product_ids, sync_public_catalog,
)


@override_settings(
    KOFAD_CLOUDINARY_ENABLED=True,
    CLOUDINARY_CLOUD_NAME="kofad-test",
    CLOUDINARY_API_KEY="isolated-public-test-key",
    CLOUDINARY_API_SECRET="isolated-private-test-secret",
)
class CloudinaryMirrorTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("marketplace.cloudinary_images.requests.post")
    def test_signed_upload_exposes_no_secret_and_accepts_only_matching_cdn(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {
            "secure_url": "https://res.cloudinary.com/kofad-test/image/upload/v1/kofad-products/abc.webp",
        })
        url = mirror_public_image(b"test-compressed-public-image")
        self.assertIn("res.cloudinary.com/kofad-test/", url)
        self.assertEqual(post.call_count, 1)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["timeout"], (3, 12))
        self.assertEqual(kwargs["data"]["api_key"], "isolated-public-test-key")
        self.assertEqual(len(kwargs["data"]["signature"]), 40)
        self.assertNotIn("isolated-private-test-secret", str(kwargs))
        self.assertEqual(kwargs["files"]["file"][2], "image/webp")

    @patch("marketplace.cloudinary_images.requests.post")
    def test_invalid_url_and_provider_failure_keep_database_original(self, post):
        for url in ("http://res.cloudinary.com/kofad-test/image/upload/x",
                    "https://evil.example/image/upload/v1/x",
                    "https://res.cloudinary.com/other/image/upload/v1/x"):
            post.return_value = Mock(status_code=200, json=lambda u=url: {"secure_url": u})
            self.assertEqual(mirror_public_image(b"public-webp"), "")
        post.side_effect = requests.Timeout("do not expose cloud credentials")
        self.assertEqual(mirror_public_image(b"public-webp"), "")

    @patch("marketplace.cloudinary_images.requests.post")
    def test_disabled_and_over_budget_never_send_upload(self, post):
        with override_settings(KOFAD_CLOUDINARY_ENABLED=False):
            self.assertEqual(mirror_public_image(b"public-webp"), "")
        with patch("marketplace.cloudinary_images._reserve_upload", return_value=False):
            self.assertEqual(mirror_public_image(b"public-webp"), "")
        post.assert_not_called()

    @patch("marketplace.cloudinary_images.mirror_public_image", return_value="https://cdn.invalid/test")
    @patch("marketplace.services.compress_market_image",
           return_value=(b"database-original", b"database-thumb", "image/webp"))
    def test_new_product_retains_database_image_even_if_mirrored(self, compress, mirror):
        from marketplace.services import save_listing_image
        listing = MarketListing()
        save_listing_image(listing, Mock(name="public-upload.webp"))
        self.assertEqual(listing.image_data, b"database-original")
        self.assertEqual(listing.image_thumb, b"database-thumb")
        self.assertEqual(listing.image_url, "https://cdn.invalid/test")
        compress.assert_called_once()
        mirror.assert_called_once_with(b"database-original")


@override_settings(
    ALLOWED_HOSTS=["testserver", "market.kofadimpex.com", "staff.kofadimpex.com"],
    KOFAD_QDRANT_ENABLED=True,
    KOFAD_QDRANT_URL="https://testing.eu-central.aws.cloud.qdrant.io",
    KOFAD_QDRANT_API_KEY="isolated-test-qdrant-api-key-long",
)
class QdrantPublicCatalogTests(TestCase):
    def setUp(self):
        cache.clear()
        product = Product.objects.create(
            sku="QD-001", name="Public White Rice", retail_unit=Decimal("36.00"), active=True,
        )
        self.listing = MarketListing.objects.create(
            product=product, enabled=True, title="Ghana White Rice",
        )

    @patch("marketplace.qdrant_products.requests.post")
    def test_public_enhanced_query_contains_no_customer_data(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {
            "result": {"points": [{"payload": {"listing_id": self.listing.pk}}]}
        })
        self.assertEqual(semantic_product_ids("rice flour"), [self.listing.pk])
        self.assertEqual(post.call_count, 1)
        args, kwargs = post.call_args
        self.assertEqual(args[0],
                         f"https://testing.eu-central.aws.cloud.qdrant.io/collections/{COLLECTION}/points/query")
        self.assertEqual(kwargs["json"]["query"], {"text": "rice flour", "model": MODEL})
        self.assertNotIn("customer", str(kwargs["json"]))
        self.assertEqual(kwargs["timeout"], (2, 5))

    @patch("marketplace.qdrant_products.requests.post")
    def test_private_and_invalid_terms_never_reach_qdrant(self, post):
        for text in ("someone@example.com", "My password is xyz", "Order 1234567890",
                     "Can you call 0241234567?", "", "<script>private</script>"):
            self.assertEqual(semantic_product_ids(text), [])
        with override_settings(KOFAD_QDRANT_URL="http://127.0.0.1:6333"):
            self.assertFalse(qdrant_configured())
            self.assertEqual(semantic_product_ids("rice flour"), [])
        post.assert_not_called()

    @patch("marketplace.qdrant_products.requests.post")
    def test_provider_outage_is_an_empty_suggestion_not_an_error(self, post):
        post.side_effect = requests.Timeout("simulated")
        self.assertEqual(semantic_product_ids("rice flour"), [])

    @patch("marketplace.qdrant_products.semantic_product_ids")
    def test_only_explicit_smart_search_invokes_qdrant_and_rechecks_live_sql(self, suggest):
        suggest.return_value = [self.listing.pk, 999999]
        normal = self.client.get("/market/?q=unmatchedkeyword", HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(normal.status_code, 200)
        self.assertContains(normal, "Try enhanced product search")
        suggest.assert_not_called()
        enhanced = self.client.get(
            "/market/?q=unmatchedkeyword&smart=1", HTTP_HOST="market.kofadimpex.com",
        )
        self.assertEqual(enhanced.status_code, 200)
        self.assertContains(enhanced, "Ghana White Rice")
        self.assertContains(enhanced, "Related published products")
        suggest.assert_called_once_with("unmatchedkeyword")

    @patch("marketplace.qdrant_products.requests.put")
    @patch("marketplace.qdrant_products.requests.get")
    def test_sync_includes_only_published_products(self, get, put):
        get.return_value = Mock(status_code=404)
        put.return_value = Mock(status_code=200)
        count = sync_public_catalog()
        self.assertEqual(count, 1)
        self.assertEqual(put.call_count, 2)
        points = put.call_args.kwargs["json"]["points"]
        self.assertEqual(points[0]["id"], self.listing.pk)
        self.assertIn(self.listing.display_name, points[0]["vector"]["text"])
        self.assertEqual(points[0]["payload"], {"listing_id": self.listing.pk})
