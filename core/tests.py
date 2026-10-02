import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections, transaction, DatabaseError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from . import services as s
from .models import Access, Audit, Branch, Company, Document, Line, Movement, Party, Payment, Product, Stock


class Fixtures:
    def setup_data(self):
        self.user = User.objects.create_superuser("owner", "owner@example.test", "test-password-long-enough")
        self.reviewer = User.objects.create_superuser("reviewer", "reviewer@example.test", "test-password-long-enough")
        self.branch = Branch.objects.create(name="Main", code="main")
        self.other = Branch.objects.create(name="Warehouse", code="warehouse")
        Company.objects.create()
        self.product = Product.objects.create(name="Test carton", sku="TEST", pack_size=12, retail_unit="50",
            retail_pack="550", wholesale_pack="480", cost="20")
        Stock.objects.create(branch=self.branch, product=self.product, quantity=240)
        self.customer = Party.objects.create(branch=self.branch, kind="customer", name="Customer", phone="test", credit_limit=5000)
        self.supplier = Party.objects.create(branch=self.branch, kind="supplier", name="Supplier", phone="test")

    def sale(self, quantity=1, mode="retail_unit", payments=None, **kwargs):
        payload = {"items":[{"product":self.product.pk, "mode":mode, "quantity":quantity}],
                   "payments":payments if payments is not None else [{"method":"cash", "amount":str(Decimal(50) * Decimal(str(quantity)))}], **kwargs}
        return s.post_trade(self.user, self.branch, payload, uuid.uuid4())

    def authenticate_client(self, user=None):
        user = user or self.user
        self.client.force_login(user)
        user.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = user.access.session_version
        session["mfa_ok"] = True
        session["branch"] = self.branch.pk
        session.save()


class BusinessTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def test_pack_math(self):
        self.sale(5)
        self.sale(3, "wholesale_pack", [{"method":"cash","amount":"1440"}])
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 199)

    def test_disabled_mode_and_negative_quantity(self):
        for quantity, mode in [(1,"wholesale_unit"), (-1,"retail_unit"), (1.5,"retail_unit")]:
            with self.assertRaises(ValidationError):
                self.sale(quantity, mode)
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)

    def test_oversell_rolls_back_everything(self):
        with self.assertRaises(ValidationError):
            self.sale(241)
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 240)

    def test_idempotency_and_mismatched_replay(self):
        key = uuid.uuid4()
        payload = {"items":[{"product":self.product.pk,"mode":"retail_unit","quantity":1}], "payments":[{"method":"cash","amount":"50"}]}
        one = s.post_trade(self.user,self.branch,payload,key)
        two = s.post_trade(self.user,self.branch,payload,key)
        self.assertEqual(one.pk,two.pk)
        self.assertEqual(Movement.objects.count(),1)
        payload["items"][0]["quantity"] = 2
        with self.assertRaises(ValidationError):
            s.post_trade(self.user,self.branch,payload,key)

    def test_credit_requires_customer_due_date_and_limit(self):
        with self.assertRaises(ValidationError):
            self.sale(payments=[])
        with self.assertRaises(ValidationError):
            self.sale(payments=[],party=self.customer.pk)
        self.customer.credit_limit = 20
        self.customer.save()
        with self.assertRaises(ValidationError):
            self.sale(payments=[],party=self.customer.pk,due_date=timezone.localdate().isoformat())

    def test_credit_collection_and_overallocation(self):
        invoice = self.sale(2,payments=[{"method":"cash","amount":"20"}],party=self.customer.pk,due_date=timezone.localdate().isoformat())
        payment = s.post_payment(self.user,self.branch,{"invoice":str(invoice.pk),"amount":"30","method":"momo"},uuid.uuid4())
        self.assertEqual(s.balance(invoice),Decimal("50"))
        with self.assertRaises(ValidationError):
            s.post_payment(self.user,self.branch,{"invoice":str(invoice.pk),"amount":"51","method":"cash"},uuid.uuid4())
        self.assertEqual(payment.allocations.count(),1)

    def test_return_credit_then_refund_and_quantity_limit(self):
        invoice = self.sale(2,payments=[{"method":"cash","amount":"20"}],party=self.customer.pk,due_date=timezone.localdate().isoformat())
        returned = s.post_return(self.user,self.branch,{"line":invoice.lines.get().pk,"quantity":2,"reason":"Customer returned goods","method":"cash"},uuid.uuid4())
        self.assertEqual(returned.paid,Decimal("20"))
        self.assertEqual(s.balance(invoice),Decimal("0"))
        self.assertEqual(Stock.objects.get(branch=self.branch,product=self.product).quantity,240)
        with self.assertRaises(ValidationError):
            s.post_return(self.user,self.branch,{"line":invoice.lines.get().pk,"quantity":1,"reason":"Duplicate return","method":"cash"},uuid.uuid4())

    def test_mixed_payments_and_overpayment(self):
        sale = self.sale(2,payments=[{"method":"cash","amount":"25"},{"method":"momo","amount":"75"}])
        self.assertEqual(sale.payments.count(),2)
        with self.assertRaises(ValidationError):
            self.sale(payments=[{"method":"cash","amount":"51"}])

    def test_purchase_pack_receipt(self):
        doc = s.post_trade(self.user,self.branch,{"party":self.supplier.pk,"items":[{"product":self.product.pk,
            "mode":"retail_pack","quantity":100,"price":"240"}],"payments":[{"method":"bank","amount":"24000"}]},uuid.uuid4(),"purchase")
        self.assertEqual(Stock.objects.get(branch=self.branch,product=self.product).quantity,1440)
        self.assertEqual(doc.payments.get().direction,-1)

    def test_transfer_approval_dispatch_receive(self):
        op = s.request_operation(self.user,self.branch,{"kind":"transfer","product":self.product.pk,
            "quantity":12,"destination":self.other.pk,"reason":"Replenish warehouse"})
        with self.assertRaises(ValidationError):
            s.advance_operation(self.user,op.pk,"approve")
        s.advance_operation(self.reviewer,op.pk,"approve")
        self.assertEqual(Stock.objects.get(branch=self.branch,product=self.product).quantity,240)
        s.advance_operation(self.user,op.pk,"dispatch")
        self.assertEqual(Stock.objects.get(branch=self.branch,product=self.product).quantity,228)
        self.assertFalse(Stock.objects.filter(branch=self.other,product=self.product).exists())
        s.advance_operation(self.user,op.pk,"receive")
        self.assertEqual(Stock.objects.get(branch=self.other,product=self.product).quantity,12)
        s.advance_operation(self.user,op.pk,"receive")
        self.assertEqual(Stock.objects.get(branch=self.other,product=self.product).quantity,12)

    def test_closing_and_independent_verification(self):
        self.sale()
        closing = s.submit_closing(self.user,self.branch,timezone.localdate(),{"cash":"50"},"")
        with self.assertRaises(ValidationError):
            self.sale()
        with self.assertRaises(ValidationError):
            s.verify_closing(self.user,closing)
        s.verify_closing(self.reviewer,closing)
        closing.refresh_from_db()
        self.assertEqual(closing.verified_by,self.reviewer)

    def test_negative_channel_net_can_be_reconciled(self):
        s.post_expense(self.user,self.branch,{"amount":"20","method":"cash","note":"Transport expense"},uuid.uuid4())
        closing = s.submit_closing(self.user,self.branch,timezone.localdate(),{"cash":"-20"},"")
        self.assertEqual(closing.expected["cash"],"-20.00")

    def test_branch_scope_and_permissions(self):
        cashier = User.objects.create_user("cashier",password="cashier-long-password")
        cashier.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        cashier.access.branches.add(self.other)
        with self.assertRaises(PermissionDenied):
            s.permit(cashier,self.branch,"operate_sales")
        with self.assertRaises(PermissionDenied):
            s.permit(cashier,self.other,"operate_finance")

    def test_money_rejects_nan_infinity_and_fractional_cents(self):
        for value in ("NaN","Infinity","-1","1.001","999999999999999999999"):
            with self.assertRaises(ValidationError):
                s.money(value)

    def test_session_revoked_on_security_change(self):
        self.authenticate_client()
        self.assertEqual(self.client.get("/").status_code,200)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.client.get("/").status_code,302)

    def test_all_pages_render(self):
        self.authenticate_client()
        for path in ["/","/inventory/","/sales/new/","/purchasing/","/documents/","/parties/",
                     "/finance/","/returns/","/operations/","/closings/","/reports/","/audit/","/settings/","/communications/",
                     "/products/new/","/parties/new/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code,200)
        doc = self.sale()
        self.assertEqual(self.client.get(f"/documents/{doc.pk}/").status_code,200)
        self.assertEqual(self.client.get(f"/parties/{self.customer.pk}/statement/").status_code,200)

    def test_exports_are_valid_files(self):
        self.sale()
        self.authenticate_client()
        from openpyxl import load_workbook
        from docx import Document as WordDocument
        for format in ("pdf","xlsx","docx","csv"):
            response = self.client.get(f"/reports/export/{format}/")
            self.assertEqual(response.status_code,200)
            if format == "pdf":
                self.assertTrue(response.content.startswith(b"%PDF"))
            if format == "xlsx":
                self.assertEqual(load_workbook(BytesIO(response.content)).active["A2"].value,"Reference")
            if format == "docx":
                self.assertEqual(len(WordDocument(BytesIO(response.content)).tables),1)

    def test_csrf_is_enforced(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/login/",{"username":"owner","password":"test-password-long-enough"}).status_code,403)

    def test_login_rate_limit(self):
        for _ in range(5):
            self.client.post("/login/",{"username":"owner","password":"wrong"})
        response = self.client.post("/login/",{"username":"owner","password":"test-password-long-enough"})
        self.assertContains(response,"Too many attempts")

    def test_login_session_survives_last_login_update(self):
        response = self.client.post("/login/",{"username":"owner","password":"test-password-long-enough"})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get("/mfa/").url,"/")
        self.assertEqual(self.client.get("/").status_code,200)

    def test_malformed_report_range_returns_400(self):
        self.authenticate_client()
        self.assertEqual(self.client.get("/reports/?start=not-a-date").status_code,400)


class ConcurrencyTests(Fixtures, TransactionTestCase):
    def setUp(self):
        self.setup_data()
        Stock.objects.filter(branch=self.branch,product=self.product).update(quantity=1)

    def test_two_buyers_cannot_oversell_last_unit(self):
        def attempt(_):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                branch = Branch.objects.get(pk=self.branch.pk)
                s.post_trade(user,branch,{"items":[{"product":self.product.pk,"mode":"retail_unit","quantity":1}],
                    "payments":[{"method":"cash","amount":"50"}]},uuid.uuid4())
                return "posted"
            except ValidationError:
                return "blocked"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt,range(2)))
        self.assertCountEqual(results,["posted","blocked"])
        self.assertEqual(Document.objects.count(),1)
        self.assertEqual(Stock.objects.get(branch=self.branch,product=self.product).quantity,0)


class LedgerIntegrityTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def test_posted_document_cannot_be_rewritten(self):
        doc = self.sale()
        with self.assertRaises(DatabaseError), transaction.atomic():
            Document.objects.filter(pk=doc.pk).update(total=0)

    def test_ledger_entries_cannot_be_deleted(self):
        self.sale()
        for model in (Line, Payment, Movement, Audit):
            with self.subTest(model=model.__name__):
                with self.assertRaises(DatabaseError), transaction.atomic():
                    model.objects.all().delete()

    def test_signed_closing_cannot_be_recounted(self):
        self.sale()
        closing = s.submit_closing(self.user,self.branch,timezone.localdate(),{"cash":"50"},"")
        with self.assertRaises(DatabaseError), transaction.atomic():
            type(closing).objects.filter(pk=closing.pk).update(counted={"cash":"0"})


class CorrectionTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def test_payment_reversal_restores_debt_and_channel(self):
        sale = self.sale(payments=[],party=self.customer.pk,due_date=timezone.localdate().isoformat())
        payment = s.post_payment(self.user,self.branch,{"invoice":str(sale.pk),"amount":"50","method":"momo"},uuid.uuid4())
        request = s.request_correction(self.user,self.branch,payment.pk,"Wrong payment reference recorded")
        with self.assertRaises(ValidationError):
            s.review_correction(self.user,self.branch,request.pk,True)
        s.review_correction(self.reviewer,self.branch,request.pk,True)
        self.assertEqual(s.balance(sale),Decimal("50"))
        self.assertEqual(s.channel_totals(self.branch,timezone.localdate())["momo"],Decimal("0"))
        with self.assertRaises(ValidationError):
            s.review_correction(self.reviewer,self.branch,request.pk,True)

    def test_void_sale_preserves_original_and_restores_stock(self):
        sale = self.sale(2)
        request = s.request_correction(self.user,self.branch,sale.pk,"Duplicate counter sale entered")
        s.review_correction(self.reviewer,self.branch,request.pk,True)
        sale.refresh_from_db()
        self.assertEqual(sale.total,Decimal("100"))
        self.assertEqual(Stock.objects.get(branch=self.branch,product=self.product).quantity,240)
        self.assertEqual(s.channel_totals(self.branch,timezone.localdate())["cash"],Decimal("0"))

    def test_expense_reversal_preserves_evidence(self):
        expense = s.post_expense(self.user,self.branch,{"amount":"20","method":"bank","note":"Office supply expense"},uuid.uuid4())
        request = s.request_correction(self.user,self.branch,expense.pk,"Incorrect supplier expense posted")
        result = s.review_correction(self.reviewer,self.branch,request.pk,True)
        self.assertEqual(result.posted.original,expense)
        self.assertEqual(s.channel_totals(self.branch,timezone.localdate())["bank"],Decimal("0"))


class ReportingTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
    def test_report_families_and_exports(self):
        self.sale(payments=[],party=self.customer.pk,due_date=timezone.localdate().isoformat())
        self.authenticate_client()
        for family in ("register","sales","inventory","aging"):
            self.assertEqual(self.client.get("/reports/?family="+family).status_code,200)
            self.assertEqual(self.client.get("/reports/export/pdf/?family="+family).status_code,200)
    def test_profit_uses_original_standard_cost(self):
        from .reporting import build_report
        self.sale(2)
        self.product.cost = Decimal("30")
        self.product.save()
        rows,_ = build_report(self.branch,timezone.localdate(),timezone.localdate(),"sales")
        self.assertEqual(rows[0]["profit"],Decimal("60"))
    def test_global_search_scopes_transactions(self):
        doc = self.sale()
        self.authenticate_client()
        response = self.client.get("/search/",{"q":doc.reference})
        self.assertContains(response,doc.reference)


class OwnerSecurityTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
    def test_company_manager_has_no_authenticator_gate(self):
        owner = User.objects.create_user("business-owner",password="business-owner-password")
        owner.user_permissions.add(Permission.objects.get(codename="manage_company"))
        owner.access.branches.add(self.branch)
        self.client.post("/login/",{"username":"business-owner","password":"business-owner-password"})
        self.assertEqual(self.client.get("/").url,"/inventory/")
    def test_cashier_cannot_search_supplier_contacts(self):
        cashier = User.objects.create_user("limited-cashier",password="limited-cashier-password")
        cashier.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        cashier.access.branches.add(self.branch)
        self.authenticate_client(cashier)
        self.assertNotContains(self.client.get("/search/",{"q":"Supplier"}),self.supplier.phone)
