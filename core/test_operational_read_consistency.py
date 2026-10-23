"""Read-path optimizations must preserve business debt and branch isolation."""
from decimal import Decimal

from django.conf import settings
from django.test import RequestFactory, TestCase

from core import services
from core.context import shell
from core.models import Allocation, Document
from core.tests import Fixtures
from marketplace.models import Conversation, ConversationMessage


class OperationalReadConsistencyTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.authenticate_client()

    def _document(self, *, kind, party, total, paid, ref):
        return Document.objects.create(
            reference=ref, branch=self.branch, kind=kind, party=party,
            total=Decimal(total), paid=Decimal(paid), created_by=self.user,
        )

    def test_contact_page_agrees_with_ledger_debt_after_allocations(self):
        sale = self._document(
            kind="sale", party=self.customer, total="120.00",
            paid="10.00", ref="QA-CONTACT-SALE-001",
        )
        collection = self._document(
            kind="collection", party=self.customer, total="25.00",
            paid="25.00", ref="QA-CONTACT-PAY-001",
        )
        Allocation.objects.create(payment_document=collection, invoice=sale, amount=Decimal("25.00"))
        response = self.client.get("/parties/")
        self.assertEqual(response.status_code, 200)
        rows = response.context["rows"]
        current = next(row["debt"] for row in rows if row["party"].pk == self.customer.pk)
        self.assertEqual(current, Decimal("85.00"))
        self.assertEqual(current, services.party_debt(self.customer))

    def test_supplier_debt_uses_existing_ledger_semantics(self):
        purchase = self._document(
            kind="purchase", party=self.supplier, total="90.00",
            paid="15.00", ref="QA-CONTACT-PUR-001",
        )
        payment = self._document(
            kind="supplier_payment", party=self.supplier, total="20.00",
            paid="20.00", ref="QA-CONTACT-SUP-001",
        )
        Allocation.objects.create(payment_document=payment, invoice=purchase, amount=Decimal("20.00"))
        response = self.client.get("/parties/?kind=supplier")
        self.assertEqual(response.status_code, 200)
        current = next(
            row["debt"] for row in response.context["rows"]
            if row["party"].pk == self.supplier.pk
        )
        self.assertEqual(current, Decimal("55.00"))
        self.assertEqual(current, services.party_debt(self.supplier))

    def test_unread_badge_excludes_other_locations(self):
        current = Conversation.objects.create(branch=self.branch, subject="Local help")
        remote = Conversation.objects.create(branch=self.other, subject="Other branch help")
        for conversation in (current, remote):
            ConversationMessage.objects.create(
                conversation=conversation, sender_type="customer",
                body="Unread request", read_by_staff=False,
            )
        request = RequestFactory().get("/workspace/")
        request.user = self.user
        request.session = {"branch": self.branch.pk}
        self.assertEqual(shell(request)["market_unread"], 1)

    def test_smtp_configuration_never_enables_tls_and_ssl_simultaneously(self):
        self.assertIn(settings.KOFAD_SMTP_SECURITY, {"ssl", "starttls"})
        self.assertNotEqual(settings.EMAIL_USE_TLS, settings.EMAIL_USE_SSL)
