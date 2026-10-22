"""A synthetic SKU audit is evidence-only, never a data mutation."""
import io
import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from core.models import Branch, Company, Product, Stock
from marketplace.models import MarketListing


class SyntheticCatalogueReadOnlyTests(TestCase):
    def test_audit_identifies_low_price_without_mutating_inventory(self):
        User.objects.create_superuser(
            "audit-owner", "audit@example.test", "long-unused-test-password"
        )
        Company.objects.create()
        branch = Branch.objects.create(code="main", name="Main")
        item = Product.objects.create(
            sku="SHOW-001", name="Ceramic dinner plate set",
            base_unit="set", cost=Decimal("10.35"),
            retail_unit=Decimal("1.02"),
        )
        Stock.objects.create(product=item, branch=branch, quantity=119)
        listing = MarketListing.objects.create(product=item, enabled=True)
        before_counts = (
            Product.objects.count(), Stock.objects.count(), MarketListing.objects.count()
        )
        output = io.StringIO()
        call_command("audit_showcase_market", stdout=output)
        report = json.loads(output.getvalue())
        self.assertTrue(report["read_only"])
        self.assertEqual(report["summary"]["enabled_market_listings"], 1)
        self.assertEqual(report["summary"]["enabled_below_cost_listings"], 1)
        self.assertEqual(report["products"][0]["sku"], "SHOW-001")
        self.assertEqual(report["products"][0]["stock_base_units"], 119)
        self.assertEqual(report["products"][0]["published_price_ghs"], "1.02")
        self.assertEqual(
            before_counts,
            (Product.objects.count(), Stock.objects.count(), MarketListing.objects.count()),
        )
        listing.refresh_from_db()
        self.assertTrue(listing.enabled)
        item.refresh_from_db()
        self.assertEqual(item.retail_unit, Decimal("1.02"))

    def test_non_show_products_not_included(self):
        Product.objects.create(sku="REAL-100", name="Real product", cost=Decimal("2"))
        out = io.StringIO()
        call_command("audit_showcase_market", stdout=out)
        report = json.loads(out.getvalue())
        self.assertEqual(report["summary"]["show_prefix_products"], 0)
        self.assertEqual(report["products"], [])
