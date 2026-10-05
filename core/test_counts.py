import uuid
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from . import counts, services as s
from .models import Closing, Movement, Stock, StockCountLine
from .tests import Fixtures


class CountTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def start(self):
        return counts.start_count(self.user, self.branch, uuid.uuid4())

    def submit(self, count, quantity="235"):
        return counts.save_count(self.user, self.branch, count.pk,
            {str(line.pk): (quantity, "") for line in count.lines.all()}, "Aisle one", True)

    def test_blind_count_approval_posts_once(self):
        count = self.start()
        self.submit(count)
        counts.review_count(self.reviewer, self.branch, count.pk, "approve", "Recount verified")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 235)
        self.assertEqual(Movement.objects.get(reference=str(count.pk)).delta, -5)
        with self.assertRaises(ValidationError):
            counts.review_count(self.reviewer, self.branch, count.pk, "approve", "Recount verified")
        self.assertEqual(Movement.objects.filter(reference=str(count.pk)).count(), 1)

    def test_zero_is_a_valid_count(self):
        count = self.start()
        self.submit(count, "0")
        counts.review_count(self.reviewer, self.branch, count.pk, "approve", "Empty shelf verified")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 0)

    def test_partial_sheet_saves_but_cannot_submit(self):
        count = self.start()
        values = {str(count.lines.get().pk): ("", "")}
        counts.save_count(self.user, self.branch, count.pk, values)
        with self.assertRaises(ValidationError):
            counts.save_count(self.user, self.branch, count.pk, values, submit=True)
        count.refresh_from_db()
        self.assertEqual(count.status, "draft")

    def test_counter_cannot_self_approve_or_edit_submitted(self):
        count = self.start()
        self.submit(count)
        with self.assertRaises(ValidationError):
            counts.review_count(self.user, self.branch, count.pk, "approve", "Self approval")
        with self.assertRaises(ValidationError):
            self.submit(count, "240")
        with self.assertRaises(DatabaseError), transaction.atomic():
            count.lines.update(counted=240)

    def test_sale_after_start_requires_new_count(self):
        count = self.start()
        self.submit(count)
        self.sale()
        with self.assertRaisesMessage(ValidationError, "Stock moved"):
            counts.review_count(self.reviewer, self.branch, count.pk, "approve", "Looks correct")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 239)
        self.assertFalse(Movement.objects.filter(reference=str(count.pk)).exists())

    def test_net_zero_movements_still_invalidate_snapshot(self):
        count = self.start()
        self.submit(count)
        with transaction.atomic():
            s.lock_branch(self.branch)
            s.stock_move(self.user, self.branch, self.product, -1, "test", "Removed")
            s.stock_move(self.user, self.branch, self.product, 1, "test", "Replaced")
        with self.assertRaisesMessage(ValidationError, "Stock moved"):
            counts.review_count(self.reviewer, self.branch, count.pk, "approve", "Looks correct")

    def test_key_reuse_and_cross_branch_access(self):
        key = uuid.uuid4()
        count = counts.start_count(self.user, self.branch, key)
        self.assertEqual(counts.start_count(self.user, self.branch, key).pk, count.pk)
        with self.assertRaises(ValidationError):
            counts.start_count(self.user, self.other, key)
        staff = User.objects.create_user("counter", password="long-test-password")
        staff.user_permissions.add(Permission.objects.get(codename="operate_inventory"))
        staff.access.branches.add(self.other)
        with self.assertRaises(PermissionDenied):
            counts.start_count(staff, self.branch, uuid.uuid4())

    def test_other_counter_cannot_change_sheet(self):
        count = self.start()
        with self.assertRaises(PermissionDenied):
            counts.save_count(self.reviewer, self.branch, count.pk, {})
        with self.assertRaises(PermissionDenied):
            counts.review_count(self.reviewer, self.branch, count.pk, "cancel")

    def test_rejected_and_cancelled_counts_never_move_stock(self):
        count = self.start()
        self.submit(count)
        counts.review_count(self.reviewer, self.branch, count.pk, "reject", "Please recount")
        other = self.start()
        counts.review_count(self.user, self.branch, other.pk, "cancel")
        self.assertFalse(Movement.objects.exists())
        with self.assertRaises(DatabaseError), transaction.atomic():
            type(count).objects.filter(pk=count.pk).update(status="draft")

    def test_invalid_quantities_roll_back(self):
        for value in ["-1", "1.5", "NaN", "2000000001", "²"]:
            count = self.start()
            with self.assertRaises(ValidationError):
                self.submit(count, value)
            self.assertIsNone(count.lines.get().counted)

    def test_snapshot_cannot_be_changed(self):
        count = self.start()
        with self.assertRaises(DatabaseError), transaction.atomic():
            count.lines.update(expected=0)
        with self.assertRaises(DatabaseError), transaction.atomic():
            count.lines.all().delete()

    def test_closed_day_blocks_approval(self):
        count = self.start()
        self.submit(count)
        Closing.objects.create(branch=self.branch, date=timezone.localdate(), expected={}, counted={}, submitted_by=self.user)
        with self.assertRaises(ValidationError):
            counts.review_count(self.reviewer, self.branch, count.pk, "approve", "Looks correct")
        self.assertFalse(Movement.objects.exists())

    def test_blind_page_and_review_evidence(self):
        count = self.start()
        self.authenticate_client()
        response = self.client.get(f"/stock-counts/{count.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "System at start")
        self.assertNotContains(response, ">240<")
        line = count.lines.get()
        response = self.client.post(f"/stock-counts/{count.pk}/", {
            "action": "submit", f"quantity_{line.pk}": "235"})
        self.assertEqual(response.status_code, 302)
        result_page = self.client.get(f"/stock-counts/{count.pk}/")
        self.assertContains(result_page, ">240<")
        self.assertContains(result_page, "Matches system")
        self.assertContains(result_page, "Differences")
        self.assertContains(result_page, "Short")
        self.assertNotContains(result_page, "Observation")
        for format, content_type in [
            ("csv", "text/csv"),
            ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            ("pdf", "application/pdf"),
        ]:
            exported = self.client.get(f"/stock-counts/{count.pk}/export/{format}/")
            self.assertEqual(exported.status_code, 200)
            self.assertTrue(exported["Content-Type"].startswith(content_type))
        self.authenticate_client(self.reviewer)
        response = self.client.post(f"/stock-counts/{count.pk}/", {"action": "approve", "review_note": "Recount verified"})
        self.assertEqual(response.status_code, 302)
        self.assertContains(self.client.get(f"/stock-counts/{count.pk}/"), "Approved")


class CountConcurrencyTests(Fixtures, TransactionTestCase):
    def setUp(self):
        self.setup_data()

    def test_two_reviewers_post_only_one_adjustment(self):
        count = counts.start_count(self.user, self.branch, uuid.uuid4())
        counts.save_count(self.user, self.branch, count.pk,
            {str(count.lines.get().pk): ("235", "")}, submit=True)
        def approve():
            close_old_connections()
            try:
                counts.review_count(User.objects.get(pk=self.reviewer.pk), self.branch, count.pk, "approve", "Verified independently")
                return "posted"
            except ValidationError:
                return "blocked"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: approve(), range(2)))
        self.assertCountEqual(outcomes, ["posted", "blocked"])
        self.assertEqual(StockCountLine.objects.get(count=count).counted, 235)
        self.assertEqual(Movement.objects.filter(reference=str(count.pk)).count(), 1)
