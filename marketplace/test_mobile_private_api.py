"""Native KOFAD read APIs never cross customer/branch boundaries or accept cookie-only sessions."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from core.tests import Fixtures
from marketplace import services
from marketplace.mobile_auth_models import MobileDeviceSession
from marketplace.mobile_identity import _digest
from marketplace.models import CustomerAccount, OnlineOrder, OnlineOrderLine


@override_settings(
    ALLOWED_HOSTS=["testserver", "market.kofadimpex.com", "staff.kofadimpex.com"],
    PRIVILEGED_MFA_ENFORCED=False,
    KOFAD_NATIVE_AUTH_ENABLED=True,
)
class NativePrivateDataTests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()
        self.customer_a=CustomerAccount.objects.create(
            phone="0241234567", full_name="Customer A", active=True,
        )
        self.customer_a.set_password("alpha-very-long-password")
        self.customer_a.save(update_fields=["password_hash"])
        self.customer_b=CustomerAccount.objects.create(
            phone="0247654321", full_name="Customer B", active=True,
        )
        self.customer_b.set_password("beta-very-long-password")
        self.customer_b.save(update_fields=["password_hash"])
        self.mine=OnlineOrder.objects.create(
            customer=self.customer_a, branch=self.branch,
            public_reference="KFD-TEST-MINE",
            recipient_name="Customer A", phone="0241234567",
            email="person@example.test", total=Decimal("120.00"),
            subtotal=Decimal("110.00"), delivery_fee=Decimal("10.00"),
            status="preparing", payment_status="paid",
            address_line="PRIVATE ADDRESS",
        )
        self.other_order=OnlineOrder.objects.create(
            customer=self.customer_b, branch=self.other,
            public_reference="KFD-TEST-OTHER",
            recipient_name="Customer B", phone="0247654321",
            email="other@example.test", total=Decimal("60.00"),
            status="preparing", payment_status="paid",
        )
        OnlineOrderLine.objects.create(
            order=self.mine, product=self.product,
            description="A carton", sku=self.product.sku,
            mode="retail_unit", quantity=2, factor=1,
            unit_price=Decimal("55.00"), unit_cost=Decimal("20.00"),
            total=Decimal("110.00"),
        )
        self.customer_token="a" * 43
        self.staff_token="b" * 43
        self._new_mobile(customer=self.customer_a,token=self.customer_token)
        self._new_mobile(staff_user=self.user,token=self.staff_token)

    def _new_mobile(self, token, customer=None, staff_user=None):
        return MobileDeviceSession.objects.create(
            channel="customer" if customer else "staff",
            customer=customer, staff_user=staff_user,
            branch=self.branch if staff_user else None,
            customer_credential_stamp=services.customer_credential_stamp(customer) if customer else "",
            staff_access_version=staff_user.access.session_version if staff_user else None,
            access_hash=_digest(token),
            refresh_hash=_digest("refresh-" + token),
            access_expires_at=timezone.now()+timedelta(minutes=15),
            refresh_expires_at=timezone.now()+timedelta(days=2),
        )

    def _get(self, url, token=None, staff=False, origin="https://localhost"):
        return self.client.get(
            url, HTTP_HOST="staff.kofadimpex.com" if staff else "market.kofadimpex.com",
            HTTP_ORIGIN=origin, HTTP_AUTHORIZATION=("Bearer " + token) if token else "",
            secure=True,
        )

    def test_customer_order_list_excludes_other_customers_and_private_fields(self):
        path="/market/mobile/v1/orders/"
        self.assertEqual(self._get(path).status_code,401)
        resp=self._get(path,self.customer_token)
        self.assertEqual(resp.status_code,200,resp.content[:150])
        self.assertEqual(resp["Cache-Control"],"no-store, private")
        items=resp.json()["items"]
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]["id"],str(self.mine.pk))
        self.assertEqual(items[0]["total"],"120.00")
        raw=str(resp.json())
        self.assertNotIn("PRIVATE ADDRESS",raw)
        self.assertNotIn("0241234567",raw)
        self.assertNotIn("KFD-TEST-OTHER",raw)

    def test_customer_order_details_return_only_owned_order_without_cost(self):
        path=f"/market/mobile/v1/orders/{self.mine.pk}/"
        owned=self._get(path,self.customer_token)
        self.assertEqual(owned.status_code,200,owned.content[:150])
        self.assertEqual(len(owned.json()["order"]["items"]),1)
        self.assertEqual(owned.json()["order"]["items"][0]["name"],"A carton")
        self.assertNotIn("unit_cost",str(owned.json()))
        blocked=self._get(f"/market/mobile/v1/orders/{self.other_order.pk}/",self.customer_token)
        self.assertEqual(blocked.status_code,404)
        self.assertEqual(blocked.json(),{"error":"not_found"})

    def test_staff_overview_is_scoped_to_current_branch(self):
        resp=self._get("/staff/mobile/v1/overview/",self.staff_token,staff=True)
        self.assertEqual(resp.status_code,200,resp.content[:150])
        body=resp.json()
        self.assertEqual(body["branch"]["id"],self.branch.pk)
        self.assertEqual(body["modules"]["orders"]["preparing"],1)
        self.assertEqual(body["modules"]["inventory"]["low_stock_items"],0)
        self.assertNotIn("KFD-TEST-OTHER",str(body))
        self.assertNotIn("cost",str(body))

    def test_staff_without_permissions_cannot_read_operations_or_cross_roles(self):
        user=get_user_model().objects.create_user("unassigned",password="random-strong-password")
        user.access.branches.add(self.branch)
        user.access.force_password_change = False
        user.access.save(update_fields=["force_password_change"])
        token="c" * 43
        mobile_session = self._new_mobile(token=token,staff_user=user)
        from marketplace.mobile_identity import _valid_device
        self.assertTrue(user.is_active)
        self.assertTrue(self.branch.active)
        self.assertTrue(user.access.branches.filter(pk=self.branch.pk).exists())
        self.assertEqual(mobile_session.staff_access_version,user.access.session_version)
        self.assertTrue(_valid_device(mobile_session,"staff"))
        resp=self._get("/staff/mobile/v1/overview/",token,staff=True)
        self.assertEqual(resp.status_code,200,resp.content[:150])
        self.assertEqual(resp.json()["modules"],{})
        self.assertEqual(self._get("/staff/mobile/v1/overview/",self.customer_token,staff=True).status_code,401)

    def test_access_is_denied_when_token_revoked_or_feature_disabled(self):
        MobileDeviceSession.objects.filter(access_hash=_digest(self.customer_token)).update(revoked_at=timezone.now())
        self.assertEqual(self._get("/market/mobile/v1/orders/",self.customer_token).status_code,401)
        with override_settings(KOFAD_NATIVE_AUTH_ENABLED=False):
            self.assertEqual(self._get("/market/mobile/v1/orders/",self.customer_token).status_code,404)
            self.assertEqual(self._get("/staff/mobile/v1/overview/",self.staff_token,staff=True).status_code,404)

    def test_cross_origin_and_mutations_are_forbidden(self):
        bad=self._get("/market/mobile/v1/orders/",self.customer_token,origin="https://attacker.invalid")
        self.assertEqual(bad.status_code,403)
        self.assertNotIn("Access-Control-Allow-Origin",bad)
        self.assertEqual(self.client.post("/market/mobile/v1/orders/").status_code,405)
        self.assertEqual(self.client.post("/staff/mobile/v1/overview/").status_code,405)
