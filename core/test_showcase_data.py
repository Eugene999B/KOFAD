from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TransactionTestCase
from django.utils import timezone

from . import services as s
from .models import (
    Audit, Branch, Closing, Company, Document, HeldSale, Message, Party,
    Product, QuarantineItem, Stock, SupplierReturn,
)


class ShowcaseDataTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.user = User.objects.create_superuser(
            "admin", "admin@example.test", "test-password-long-enough"
        )
        self.branch = Branch.objects.create(name="Main", code="main")
        Company.objects.create(name="KOFAD IMPEX ENTERPRISE")

    def test_showcase_loader_populates_major_business_areas_and_is_idempotent(self):
        call_command("load_showcase_data", confirm_live_showcase=True)

        self.assertGreaterEqual(Product.objects.filter(sku__startswith="SHOW-").count(), 50)
        self.assertEqual(Party.objects.filter(kind="customer", name__startswith="Showcase").count(), 60)
        self.assertEqual(Party.objects.filter(kind="supplier", name__startswith="Showcase").count(), 10)
        self.assertEqual(Document.objects.filter(kind="sale", reference__startswith="SHOW-").count(), 180)
        self.assertEqual(Document.objects.filter(kind="purchase", reference__startswith="SHOW-").count(), 24)
        self.assertGreater(Document.objects.filter(kind="collection", reference__startswith="SHOW-").count(), 5)
        self.assertEqual(Document.objects.filter(kind="expense", reference__startswith="SHOW-").count(), 32)
        self.assertGreater(Closing.objects.filter(branch=self.branch).count(), 5)
        self.assertFalse(Closing.objects.filter(branch=self.branch, date=timezone.localdate()).exists())
        self.assertEqual(HeldSale.objects.filter(branch=self.branch).count(), 6)
        self.assertEqual(Message.objects.filter(source_key__startswith="showcase-message-").count(), 24)
        self.assertGreaterEqual(QuarantineItem.objects.filter(branch=self.branch).count(), 7)
        self.assertGreaterEqual(SupplierReturn.objects.filter(branch=self.branch).count(), 5)
        self.assertEqual(
            Stock.objects.filter(branch=self.branch, product__sku__startswith="SHOW-").count(),
            Product.objects.filter(sku__startswith="SHOW-").count(),
        )

        debtors = [
            party for party in Party.objects.filter(branch=self.branch, kind="customer")
            if s.party_debt(party) > 0
        ]
        self.assertGreater(len(debtors), 10)
        self.assertTrue(Audit.objects.filter(action="showcase.seed.completed").exists())

        before = {
            "products": Product.objects.count(),
            "documents": Document.objects.count(),
            "parties": Party.objects.count(),
            "closings": Closing.objects.count(),
        }
        call_command("load_showcase_data", confirm_live_showcase=True)
        after = {
            "products": Product.objects.count(),
            "documents": Document.objects.count(),
            "parties": Party.objects.count(),
            "closings": Closing.objects.count(),
        }
        self.assertEqual(before, after)
