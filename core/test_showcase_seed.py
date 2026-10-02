from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from . import debts as debt_service
from .models import (
    Branch, Closing, Company, Document, HeldSale, Message, Party, Product,
    QuarantineItem, StockCount, SupplierReturn,
)


class ShowcaseSeedTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            "admin", "admin@example.test", "test-password-long-enough"
        )
        self.branch = Branch.objects.create(name="Main", code="main")
        self.company = Company.objects.create(
            allow_credit_sales=False,
            payment_card=False,
        )

    def test_compact_showcase_seed_populates_major_workspaces_and_restores_policy(self):
        out = StringIO()
        call_command(
            "seed_showcase",
            confirm="LOAD KOFAD SAMPLE DATA",
            days=3,
            sales_per_day=2,
            product_limit=12,
            customer_limit=12,
            stdout=out,
        )

        self.assertEqual(Product.objects.filter(sku__startswith="SAMPLE-").count(), 12)
        self.assertEqual(
            Party.objects.filter(branch=self.branch, kind="customer", name__startswith="SAMPLE ·").count(),
            12,
        )
        self.assertEqual(
            Party.objects.filter(branch=self.branch, kind="supplier", name__startswith="SAMPLE ·").count(),
            8,
        )
        self.assertGreaterEqual(Document.objects.filter(branch=self.branch, kind="sale").count(), 6)
        self.assertEqual(Closing.objects.filter(branch=self.branch).count(), 3)
        self.assertGreater(HeldSale.objects.filter(branch=self.branch).count(), 0)
        self.assertGreater(Message.objects.filter(branch=self.branch, status="draft").count(), 0)
        self.assertGreater(QuarantineItem.objects.filter(branch=self.branch).count(), 0)
        self.assertGreater(SupplierReturn.objects.filter(branch=self.branch).count(), 0)
        self.assertGreater(StockCount.objects.filter(branch=self.branch).count(), 0)

        customers = Party.objects.filter(branch=self.branch, kind="customer")
        self.assertTrue(any(debt_service.customer_account_snapshot(customer)["outstanding"] > 0 for customer in customers))

        self.company.refresh_from_db()
        self.assertFalse(self.company.allow_credit_sales)
        self.assertFalse(self.company.payment_card)

        reviewer = User.objects.get(username="kofad_sample_reviewer")
        self.assertFalse(reviewer.is_active)
        self.assertFalse(reviewer.has_usable_password())
        self.assertIn("sample data loaded successfully", out.getvalue().lower())

    def test_showcase_seed_requires_confirmation_and_refuses_duplicate_load(self):
        with self.assertRaises(CommandError):
            call_command("seed_showcase", days=3, product_limit=12, customer_limit=12)

        call_command(
            "seed_showcase",
            confirm="LOAD KOFAD SAMPLE DATA",
            days=3,
            sales_per_day=2,
            product_limit=12,
            customer_limit=12,
            stdout=StringIO(),
        )
        with self.assertRaisesRegex(CommandError, "already exists"):
            call_command(
                "seed_showcase",
                confirm="LOAD KOFAD SAMPLE DATA",
                days=3,
                product_limit=12,
                customer_limit=12,
            )
