"""Public catalog semantic grounding is consented, live and publication-filtered."""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from core.models import Product
from marketplace.kofad_ai import _public_products, answer
from marketplace.models import MarketListing


@override_settings(KOFAD_GEMINI_ENABLED=False, KOFAD_OPENROUTER_FREE_ENABLED=False)
class PublicSemanticGroundingTests(TestCase):
    def setUp(self):
        self.published = Product.objects.create(
            sku="AI-SEM-PUB", name="Baking Flour", retail_unit=Decimal("29.00"), active=True,
        )
        self.unpublished = Product.objects.create(
            sku="AI-SEM-PRIVATE", name="Internal Item", retail_unit=Decimal("99.00"), active=True,
        )
        self.live = MarketListing.objects.create(
            product=self.published, enabled=True, description="Flour for baking.",
        )
        self.hidden = MarketListing.objects.create(product=self.unpublished, enabled=False)

    @patch("marketplace.qdrant_products.semantic_product_ids")
    def test_semantic_results_are_rechecked_against_live_published_catalog(self, semantic):
        semantic.return_value = [self.hidden.pk, self.live.pk]
        rows = _public_products("recommend something for baking", semantic=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Baking Flour")
        self.assertIn("baking", rows[0]["description"])
        self.assertEqual(rows[0]["price"], str(self.live.market_price))
        semantic.assert_called_once()

    @patch("marketplace.qdrant_products.semantic_product_ids")
    def test_private_order_and_no_consent_do_not_query_qdrant(self, semantic):
        answer("Where is my order?", ai_allowed=True)
        answer("Recommend something for baking", ai_allowed=False)
        semantic.assert_not_called()

    @patch("marketplace.qdrant_products.semantic_product_ids")
    def test_semantic_question_can_still_answer_locally_on_search_failure(self, semantic):
        semantic.return_value = []
        reply, source = answer("Recommend something for baking", ai_allowed=True)
        self.assertEqual(source, "local")
        self.assertIsInstance(reply, str)
