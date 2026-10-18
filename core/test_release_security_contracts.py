"""Security invariants kept separate from feature modules and older release branches.

These are deliberately exercised by the post-merge release regression workflow.
A stale feature branch must not quietly remove both the protection and its tests.
"""
from django.contrib.auth.models import Permission, User
from django.test import RequestFactory, override_settings

from core.google_oauth import _customer_login_return
from core.models import Branch
from marketplace.models import MarketReturnAttachment, MarketReturnRequest
from marketplace.tests import MarketFixtures


@override_settings(
    ALLOWED_HOSTS=["testserver", "staff.kofadimpex.com", "market.kofadimpex.com"],
)
class ReleaseSecurityContracts(MarketFixtures):
    def test_return_evidence_is_private_to_staff_assigned_to_the_order_branch(self):
        order = self.order()
        item = MarketReturnRequest.objects.create(
            order=order, customer=self.customer, reason="Private evidence",
        )
        evidence = MarketReturnAttachment.objects.create(
            request=item, original_name="evidence.png",
            mime_type="image/png", size=5, sha256="synthetic", data=b"proof",
        )
        url = f"/market/returns/attachments/{evidence.pk}/"
        other = Branch.objects.create(name="Unrelated location", code="contract-other")
        staff = User.objects.create_user("security-contract-staff", password="isolated-test-password")
        staff.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        staff.access.branches.add(other)
        self.client.force_login(staff)
        session = self.client.session
        session["access_version"] = staff.access.session_version
        session.save()

        # Global permission is insufficient to read another branch's evidence.
        self.assertEqual(self.client.get(url, HTTP_HOST="staff.kofadimpex.com").status_code, 404)
        staff.access.branches.add(self.branch)
        staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = staff.access.session_version
        session.save()
        allowed = self.client.get(url, HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.content, b"proof")

        self.client.logout()
        anonymous = self.client.get(url, HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(anonymous.status_code, 404)
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        owner = self.client.get(url, HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.content, b"proof")

    def test_customer_google_return_never_redirects_to_external_destination(self):
        factory = RequestFactory()
        poisoned = (
            "https://external.example/fake-login",
            "//external.example/continue",
            r"/\\external.example/path",
            "/market/orders/\r\nLocation: https://external.example",
            {"url": "https://external.example"},
        )
        for target in poisoned:
            with self.subTest(target=repr(target)):
                request = factory.get("/", HTTP_HOST="market.kofadimpex.com")
                request.session = {"market_after_login": target}
                self.assertEqual(_customer_login_return(request), "market_account")
                self.assertNotIn("market_after_login", request.session)

        allowed = factory.get("/", HTTP_HOST="market.kofadimpex.com")
        allowed.session = {"market_after_login": "/market/orders/"}
        self.assertEqual(_customer_login_return(allowed), "/market/orders/")
        self.assertNotIn("market_after_login", allowed.session)
