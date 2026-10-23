"""KOFAD customer AI computes only exact published prices; no guesswork or PII."""
from unittest.mock import patch

from django.test import TestCase

from marketplace.kofad_ai import answer, _grounded_shopping_comparison


PRODUCTS = [
    {"name": "Flour bag", "price": "24.50", "unit": "bag",
     "url": "https://market.kofadimpex.com/market/products/2/"},
    {"name": "Rice bag", "price": "45.00", "unit": "bag",
     "url": "https://market.kofadimpex.com/market/products/3/"},
    {"name": "Flour carton", "price": "80.00", "unit": "carton",
     "url": "https://market.kofadimpex.com/market/products/4/"},
]


class GroundedShoppingDecisionsTests(TestCase):
    def test_budget_filters_without_fabricating_listings_or_a_discount(self):
        result = _grounded_shopping_comparison("Compare flour and rice under GHS 40", PRODUCTS)
        self.assertIn("Flour bag", result)
        self.assertIn("GHS 24.50", result)
        self.assertNotIn("Rice bag", result)
        self.assertNotIn("Flour carton", result)
        self.assertIn("not a promise of physical stock", result)
        self.assertNotIn("20% off", result)

    def test_different_pack_units_are_not_misreported_as_equal_value(self):
        result = _grounded_shopping_comparison("Which is cheapest for the budget?", PRODUCTS)
        self.assertIn("different selling units", result)
        self.assertNotIn("The first option has the lowest listed price for the same", result)

    @patch("marketplace.kofad_ai._free_completion")
    @patch("marketplace.kofad_ai._public_products", return_value=PRODUCTS)
    def test_exact_price_comparison_bypasses_generating_provider(self, products, completion):
        result, source = answer("Compare flour and rice under GHS 50", ai_allowed=True)
        self.assertEqual(source, "local")
        self.assertIn("Flour bag", result)
        self.assertIn("Rice bag", result)
        self.assertNotIn("Flour carton", result)
        completion.assert_not_called()
        products.assert_called_once()

    @patch("marketplace.kofad_ai._free_completion")
    def test_ghanaian_greeting_works_offline(self, completion):
        text, source = answer("Akwaaba", ai_allowed=False)
        self.assertEqual(source, "local")
        self.assertIn("Akwaaba!", text)
        self.assertIn("What are you looking for today?", text)
        completion.assert_not_called()

    def test_empty_or_invalid_comparison_never_claims_exact_stock(self):
        self.assertIsNone(_grounded_shopping_comparison("compare rice", []))
        result = _grounded_shopping_comparison("rice under GHS 1", PRODUCTS)
        self.assertIn("None of the matching published listings", result)
        self.assertNotIn("in stock", result.lower())
