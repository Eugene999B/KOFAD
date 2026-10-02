from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from . import services as s
from .models import Closing, Movement, Stock, TransferReceipt
from .tests import Fixtures
from .transfers import receive_transfer, resolve_transfer


class TransferFixtures(Fixtures):
    def dispatched(self):
        op = s.request_operation(self.user, self.branch, {"kind": "transfer", "product": self.product.pk,
            "quantity": 12, "destination": self.other.pk, "reason": "Replenish warehouse"})
        s.advance_operation(self.reviewer, op.pk, "approve")
        s.advance_operation(self.user, op.pk, "dispatch")
        return op


class TransferReceiptTests(TransferFixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def test_short_delivery_adds_only_sellable_units(self):
        op = self.dispatched()
        receive_transfer(self.user, op.pk, 9, "Three units missing")
        op.refresh_from_db()
        self.assertEqual(op.status, "discrepancy")
        self.assertEqual(Stock.objects.get(branch=self.other, product=self.product).quantity, 9)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 228)
        self.assertEqual(op.receipt.missing, 3)

    def test_late_arrival_adds_remainder_once(self):
        op = self.dispatched()
        receive_transfer(self.user, op.pk, 9, "Three units missing")
        resolve_transfer(self.reviewer, op.pk, "arrived", "Remaining three arrived intact")
        self.assertEqual(Stock.objects.get(branch=self.other, product=self.product).quantity, 12)
        self.assertEqual(TransferReceipt.objects.get(operation=op).quantity, 9)
        with self.assertRaises(ValidationError):
            resolve_transfer(self.reviewer, op.pk, "arrived", "Remaining three arrived intact")
        self.assertEqual(Movement.objects.filter(branch=self.other, reference=str(op.pk)).count(), 2)

    def test_confirmed_loss_does_not_invent_stock(self):
        op = self.dispatched()
        receive_transfer(self.user, op.pk, 0, "All units damaged in transit")
        resolve_transfer(self.reviewer, op.pk, "loss", "Damage independently inspected")
        self.assertFalse(Stock.objects.filter(branch=self.other, product=self.product).exists())
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 228)
        self.assertEqual(TransferReceipt.objects.get(operation=op).resolution, "loss")

    def test_receiver_cannot_resolve_own_discrepancy(self):
        op = self.dispatched()
        receive_transfer(self.user, op.pk, 9, "Three units missing")
        with self.assertRaises(ValidationError):
            resolve_transfer(self.user, op.pk, "loss", "Damage independently inspected")

    def test_identical_receipt_retry_does_not_duplicate_stock(self):
        op = self.dispatched()
        for _ in range(2):
            receive_transfer(self.user, op.pk, 9, "Three units missing")
        self.assertEqual(Movement.objects.filter(branch=self.other, reference=str(op.pk)).count(), 1)
        with self.assertRaises(ValidationError):
            receive_transfer(self.user, op.pk, 12)

    def test_invalid_receipts_leave_dispatch_unchanged(self):
        op = self.dispatched()
        for quantity, note in [("13", ""), ("-1", ""), ("1.5", ""), ("9", "short"), ("", "")]:
            with self.assertRaises(ValidationError):
                receive_transfer(self.user, op.pk, quantity, note)
        self.assertFalse(TransferReceipt.objects.exists())
        self.assertFalse(Stock.objects.filter(branch=self.other).exists())

    def test_receipt_evidence_is_immutable(self):
        op = self.dispatched()
        receive_transfer(self.user, op.pk, 9, "Three units missing")
        with self.assertRaises(DatabaseError), transaction.atomic():
            TransferReceipt.objects.filter(operation=op).update(quantity=12)
        with self.assertRaises(DatabaseError), transaction.atomic():
            TransferReceipt.objects.filter(operation=op).update(unit_cost=0)
        resolve_transfer(self.reviewer, op.pk, "loss", "Loss independently verified")
        receipt = TransferReceipt.objects.get(operation=op)
        self.assertIsNotNone(receipt.loss_document_id)
        with self.assertRaises(DatabaseError), transaction.atomic():
            TransferReceipt.objects.filter(operation=op).update(resolution="arrived")
        with self.assertRaises(DatabaseError), transaction.atomic():
            TransferReceipt.objects.filter(operation=op).update(loss_document=None)

    def test_destination_branch_permission_required(self):
        op = self.dispatched()
        staff = User.objects.create_user("source-only", password="long-test-password")
        staff.user_permissions.add(Permission.objects.get(codename="operate_inventory"))
        staff.access.branches.add(self.branch)
        with self.assertRaises(PermissionDenied):
            receive_transfer(staff, op.pk, 12)

    def test_closed_destination_blocks_receipt(self):
        op = self.dispatched()
        Closing.objects.create(branch=self.other, date=timezone.localdate(), expected={}, counted={}, submitted_by=self.user)
        with self.assertRaises(ValidationError):
            receive_transfer(self.user, op.pk, 12)

    def test_closed_destination_blocks_resolution(self):
        op = self.dispatched()
        receive_transfer(self.user, op.pk, 9, "Three units missing")
        Closing.objects.create(branch=self.other, date=timezone.localdate(), expected={}, counted={}, submitted_by=self.user)
        with self.assertRaises(ValidationError):
            resolve_transfer(self.reviewer, op.pk, "arrived", "Remaining three arrived intact")

    def test_operations_screen_shows_receipt_evidence(self):
        op = self.dispatched()
        receive_transfer(self.user, op.pk, 9, "Three units missing")
        self.authenticate_client()
        response = self.client.get("/operations/")
        self.assertContains(response, "Initially received: 9")
        self.assertContains(response, "Three units missing")


class TransferConcurrencyTests(TransferFixtures, TransactionTestCase):
    def setUp(self):
        self.setup_data()

    def test_concurrent_receipt_retries_post_once(self):
        op = self.dispatched()
        def receive():
            close_old_connections()
            try:
                receive_transfer(User.objects.get(pk=self.user.pk), op.pk, 9, "Three units missing")
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: receive(), range(2)))
        self.assertEqual(Movement.objects.filter(branch=self.other, reference=str(op.pk)).count(), 1)
        self.assertEqual(Stock.objects.get(branch=self.other, product=self.product).quantity, 9)
