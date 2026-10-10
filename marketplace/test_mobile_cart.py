"""Read/write mobile baskets require real KOFAD mobile bearer identity.

No request through browser cookies, cross-channel staff tokens or an untrusted
origin can read another customer's basket or mutate checkout/ledger records.
"""
import hashlib
import json
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.tests import Fixtures
from marketplace import services
from marketplace.mobile_auth_models import MobileDeviceSession
from marketplace.models import CustomerAccount, MarketListing, NativeCartItem


@override_settings(
    ALLOWED_HOSTS=["testserver", "market.kofadimpex.com", "staff.kofadimpex.com"],
    KOFAD_NATIVE_AUTH_ENABLED=True,
    KOFAD_NATIVE_CART_ENABLED=True,
)
class NativeAccountCartTests(Fixtures, TestCase):
    route = "/market/mobile/v1/cart/"
    token = "A" * 43

    def setUp(self):
        self.setup_data()
        self.customer = CustomerAccount.objects.create(
            phone="0241119999", full_name="Native market customer", active=True,
            password_hash="nonempty-test-hash",
        )
        self.other = CustomerAccount.objects.create(
            phone="0241119988", full_name="Other customer", active=True,
            password_hash="different-test-hash",
        )
        self.listing = MarketListing.objects.create(
            product=self.product, enabled=True, price_source="retail_unit",
        )
        self.session = MobileDeviceSession.objects.create(
            channel="customer", customer=self.customer,
            customer_credential_stamp=services.customer_credential_stamp(self.customer),
            access_hash=hashlib.sha256(self.token.encode()).hexdigest(),
            refresh_hash=hashlib.sha256(("R" * 43).encode()).hexdigest(),
            access_expires_at=timezone.now() + timedelta(minutes=15),
            refresh_expires_at=timezone.now() + timedelta(days=5),
        )

    def req(self, *, token=None, method="get", body=None, origin="https://localhost"):
        client = Client()
        headers = {
            "HTTP_HOST": "market.kofadimpex.com",
            "HTTP_ORIGIN": origin,
        }
        if token:
            headers["HTTP_AUTHORIZATION"] = "Bearer " + token
        if method == "put":
            return client.put(
                self.route, data=json.dumps(body),
                content_type="application/json", secure=True, **headers,
            )
        if method == "options":
            return client.options(self.route, secure=True, **headers)
        return client.get(self.route, secure=True, **headers)

    def test_enabled_bearer_can_save_and_read_owned_cart_without_business_mutations(self):
        initial = self.req(token=self.token)
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["items"], [])
        saved = self.req(token=self.token, method="put", body={
            "items": [{"id": self.listing.id, "quantity": 2}],
        })
        self.assertEqual(saved.status_code, 200, saved.content[:400])
        self.assertEqual(saved.json()["count"], 2)
        self.assertFalse(saved.json()["checkout_ready"])
        self.assertEqual(NativeCartItem.objects.get(customer=self.customer).quantity, 2)
        self.assertEqual(self.req(token=self.token).json()["items"][0]["id"], self.listing.id)
        self.assertIn("no-store", saved["Cache-Control"])
        self.assertEqual(saved["Access-Control-Allow-Origin"], "https://localhost")
        self.assertNotIn("Access-Control-Allow-Credentials", saved)
        self.assertNotIn("supplier", str(saved.json()).lower())
        self.assertNotIn("unit_cost", str(saved.json()).lower())
        # No order, sale, reservation or payment records are written.
        from marketplace.models import OnlineOrder, StockReservation
        self.assertEqual(OnlineOrder.objects.count(), 0)
        self.assertEqual(StockReservation.objects.count(), 0)

    def test_browser_cookie_and_unauthorized_tokens_denied(self):
        self.assertEqual(self.req().status_code, 401)
        self.assertEqual(self.req(token="B" * 43).status_code, 401)
        self.assertEqual(self.req(method="put", body={"items": []}).status_code, 401)
        from django.test import Client
        browser = Client()
        session = browser.session
        session["market_customer_id"] = self.customer.pk
        session["market_credential_stamp"] = services.customer_credential_stamp(self.customer)
        session["market_session_expires_at"] = timezone.now().timestamp() + 3600
        session.save()
        self.assertEqual(browser.get(self.route, HTTP_HOST="market.kofadimpex.com", secure=True).status_code, 401)

    def test_cross_account_and_revoked_device_cannot_see_or_change_cart(self):
        NativeCartItem.objects.create(customer=self.other, listing=self.listing, quantity=3)
        result = self.req(token=self.token)
        self.assertEqual(result.json()["items"], [])
        self.req(token=self.token, method="put", body={"items":[{"id":self.listing.pk,"quantity":1}]})
        self.assertEqual(NativeCartItem.objects.get(customer=self.other).quantity, 3)
        self.session.revoked_at = timezone.now()
        self.session.save(update_fields=["revoked_at"])
        self.assertEqual(self.req(token=self.token).status_code, 401)

    def test_invalid_qty_duplicate_inactive_listing_and_unsafe_origin_are_denied(self):
        invalid = [
            {"items":[{"id":self.listing.pk, "quantity": 0}]},
            {"items":[{"id":self.listing.pk, "quantity": True}]},
            {"items":[{"id":self.listing.pk, "quantity": 21}]},
            {"items":[{"id":self.listing.pk, "quantity": 1}, {"id":self.listing.pk,"quantity":2}]},
            {"items":[{"id":"1", "quantity":1}]},
            {"items":[{"id":self.listing.pk, "quantity":1, "price":"0"}]},
            {"items":[{"id":self.listing.pk, "quantity":1}], "customer_id":self.other.pk},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.assertEqual(self.req(token=self.token, method="put", body=payload).status_code,400)
        self.assertFalse(NativeCartItem.objects.exists())
        self.listing.enabled = False
        self.listing.save(update_fields=["enabled"])
        self.assertEqual(self.req(token=self.token, method="put", body={
            "items":[{"id":self.listing.pk,"quantity":1}],
        }).status_code, 409)
        evil = self.req(token=self.token,origin="https://attacker.example")
        self.assertEqual(evil.status_code, 403)
        self.assertNotIn("Access-Control-Allow-Origin", evil)

    def test_gate_off_and_options_are_constrained(self):
        self.assertEqual(self.req(method="options").status_code, 204)
        allowed = self.req(method="options")
        self.assertEqual(allowed["Access-Control-Allow-Origin"], "https://localhost")
        self.assertIn("PUT", allowed["Access-Control-Allow-Methods"])
        evil = self.req(method="options", origin="https://evil.example")
        self.assertNotIn("Access-Control-Allow-Origin", evil)
        with override_settings(KOFAD_NATIVE_CART_ENABLED=False):
            self.assertEqual(self.req(token=self.token).status_code, 404)
            self.assertEqual(self.req(token=self.token, method="put", body={"items":[]}).status_code,404)
        with override_settings(KOFAD_NATIVE_AUTH_ENABLED=False):
            self.assertEqual(self.req(token=self.token).status_code, 404)
