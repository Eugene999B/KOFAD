import hashlib
import hmac
import io
import json
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core.models import Branch, Closing, Company, Document, Payment, Product, Stock
from .models import CustomerAccount, MarketListing, MarketPaymentAttempt, OnlineOrder
from . import services


class MarketFixtures(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main", code="main", active=True)
        Company.objects.create(name="KOFAD IMPEX ENTERPRISE", currency="GHS")
        self.staff = User.objects.create_superuser(
            "market-owner", "owner@example.test", "market-owner-password"
        )
        self.staff.access.branches.add(self.branch)
        self.product = Product.objects.create(
            name="Premium hydraulic filter",
            sku="MKT-001",
            category="Excavator parts",
            base_unit="piece",
            pack_name="box",
            pack_size=1,
            cost=Decimal("60.00"),
            retail_unit=Decimal("100.00"),
            wholesale_unit=Decimal("90.00"),
            active=True,
        )
        Stock.objects.create(branch=self.branch, product=self.product, quantity=20)
        self.listing = MarketListing.objects.create(
            product=self.product,
            enabled=True,
            featured=True,
            title="Premium hydraulic filter",
            description="Heavy-duty replacement filter for selected excavator applications.",
            price_source="retail_unit",
            image_data=b"market-large",
            image_thumb=b"market-thumb",
        )
        self.customer = CustomerAccount(
            phone="+233241234567",
            full_name="Market Customer",
            email="customer@example.test",
            verified_at=timezone.now(),
        )
        self.customer.set_password("Very-strong-customer-password-42!")
        self.customer.save()

    def order(self, *, fulfilment="pickup"):
        cleaned = {
            "fulfilment": fulfilment,
            "recipient_name": self.customer.full_name,
            "phone": self.customer.phone,
            "email": self.customer.email,
            "delivery_zone": None,
            "region": "",
            "town": "",
            "address_line": "",
            "landmark": "",
            "ghana_post_gps": "",
            "latitude": None,
            "longitude": None,
            "customer_note": "",
        }
        return services.create_order(
            self.customer,
            {str(self.listing.pk): 2},
            cleaned,
        )


class MarketPublicExperienceTests(MarketFixtures):
    def test_public_home_replaces_staff_dashboard_at_root(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Visit KOFAD Market")
        self.assertContains(response, "Staff workspace")
        self.assertEqual(self.client.get("/workspace/").status_code, 302)

    def test_only_published_products_appear_in_market(self):
        hidden = Product.objects.create(
            name="Internal only part", sku="MKT-002", base_unit="piece",
            pack_name="piece", pack_size=1, cost=1, retail_unit=2, active=True,
        )
        MarketListing.objects.create(
            product=hidden, enabled=False, price_source="retail_unit",
            image_data=b"x", image_thumb=b"x",
        )
        response = self.client.get("/market/")
        self.assertContains(response, self.product.name)
        self.assertNotContains(response, hidden.name)

    def test_unpublished_market_image_is_staff_only(self):
        self.listing.enabled = False
        self.listing.save(update_fields=["enabled"])
        self.assertEqual(
            self.client.get(f"/market/products/{self.listing.pk}/image/thumb/").status_code,
            404,
        )
        self.client.force_login(self.staff)
        session = self.client.session
        self.staff.access.refresh_from_db()
        session["access_version"] = self.staff.access.session_version
        session.save()
        response = self.client.get(f"/market/products/{self.listing.pk}/image/thumb/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_product_picture_is_normalized_to_market_webp_sizes(self):
        image = Image.new("RGB", (2200, 1300), (30, 90, 130))
        source = io.BytesIO()
        image.save(source, "PNG")
        upload = SimpleUploadedFile("camera-photo.png", source.getvalue(), content_type="image/png")
        large, thumb, mime = services.compress_market_image(upload)
        self.assertEqual(mime, "image/webp")
        self.assertLess(len(large), len(source.getvalue()))
        with Image.open(io.BytesIO(large)) as result:
            self.assertEqual(result.size, (1200, 1200))
            self.assertEqual(result.format, "WEBP")
        with Image.open(io.BytesIO(thumb)) as result:
            self.assertEqual(result.size, (480, 480))


class MarketCustomerAndCartTests(MarketFixtures):
    def test_cart_requires_customer_account_not_staff_auth(self):
        response = self.client.post(
            f"/market/cart/add/{self.listing.pk}/",
            {"quantity": "1"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/market/account/login/")

    def test_customer_login_throttles_repeated_wrong_passwords(self):
        for _ in range(5):
            self.client.post(
                "/market/account/login/",
                {"phone": "0241234567", "password": "wrong-password"},
            )
        response = self.client.post(
            "/market/account/login/",
            {"phone": "0241234567", "password": "Very-strong-customer-password-42!"},
        )
        self.assertContains(response, "Too many sign-in attempts")

    def test_order_reserves_live_kofad_stock_without_deducting_before_payment(self):
        order = self.order()
        reservation = order.reservations.get(product=self.product)
        self.assertEqual(reservation.units, 2)
        self.assertTrue(reservation.active)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 20)
        self.assertEqual(order.total, Decimal("200.00"))


class MarketPaymentTests(MarketFixtures):
    def payment_attempt(self, order, reference="KFD-TEST-PAY"):
        order.payment_reference = reference
        order.payment_status = "pending"
        order.save(update_fields=["payment_reference", "payment_status"])
        return MarketPaymentAttempt.objects.create(
            order=order,
            reference=reference,
            amount=order.total,
            currency="GHS",
            status="pending",
        )

    @override_settings(PAYSTACK_SECRET_KEY="paystack-secret-for-test")
    def test_paystack_signature_validation_uses_hmac_sha512(self):
        raw = b'{"event":"charge.success"}'
        signature = hmac.new(
            b"paystack-secret-for-test", raw, hashlib.sha512
        ).hexdigest()
        self.assertTrue(services.paystack_signature_valid(raw, signature))
        self.assertFalse(services.paystack_signature_valid(raw, "bad-signature"))

    @override_settings(PAYSTACK_SECRET_KEY="paystack-secret-for-test")
    @patch("marketplace.services.requests.post")
    def test_payment_initialization_reuses_recent_pending_checkout(self, post):
        order = self.order()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "status": True,
            "message": "Authorization URL created",
            "data": {
                "authorization_url": "https://checkout.paystack.com/test-access",
                "access_code": "test-access",
            },
        }
        post.return_value = response
        first = services.initialize_paystack(order, "https://example.test/market/payment/return/")
        second = services.initialize_paystack(order, "https://example.test/market/payment/return/")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(post.call_count, 1)

    def test_verified_momo_payment_posts_existing_kofad_sale_and_stock_movement(self):
        order = self.order()
        attempt = self.payment_attempt(order)
        result = services.finalize_payment(attempt.reference, {
            "status": "success",
            "amount": int(order.total * 100),
            "currency": "GHS",
            "channel": "mobile_money",
        })
        result.refresh_from_db()
        self.assertEqual(result.payment_status, "paid")
        self.assertEqual(result.ledger_status, "posted")
        self.assertIsNotNone(result.sale_document_id)
        document = Document.objects.get(pk=result.sale_document_id)
        self.assertEqual(document.kind, "sale")
        self.assertEqual(document.total, Decimal("200.00"))
        payment = Payment.objects.get(document=document)
        self.assertEqual(payment.method, "momo")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 18)

    def test_payment_after_daily_close_is_paid_but_not_back_posted(self):
        order = self.order()
        attempt = self.payment_attempt(order, "KFD-AFTER-CLOSE")
        Closing.objects.create(
            branch=self.branch,
            date=timezone.localdate(),
            expected={},
            counted={},
            summary={},
            submitted_by=self.staff,
        )
        result = services.finalize_payment(attempt.reference, {
            "status": "success",
            "amount": int(order.total * 100),
            "currency": "GHS",
            "channel": "card",
        })
        result.refresh_from_db()
        self.assertEqual(result.payment_status, "paid")
        self.assertEqual(result.ledger_status, "attention")
        self.assertIsNone(result.sale_document_id)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 20)
        self.assertTrue(result.reservations.filter(active=True).exists())


class MarketFulfilmentTests(MarketFixtures):
    def paid_order(self):
        order = self.order()
        reference = "KFD-HANDOVER"
        MarketPaymentAttempt.objects.create(
            order=order, reference=reference, amount=order.total,
            currency="GHS", status="pending",
        )
        return services.finalize_payment(reference, {
            "status": "success",
            "amount": int(order.total * 100),
            "currency": "GHS",
            "channel": "card",
        })

    def test_pickup_cannot_complete_without_customer_handover_code(self):
        order = self.paid_order()
        order = services.advance_order(self.staff, order, "prepare", {
            "delivery_agent_name": "", "delivery_agent_phone": "", "handover_code": "", "note": "",
        })
        order = services.advance_order(self.staff, order, "ready_pickup", {
            "delivery_agent_name": "", "delivery_agent_phone": "", "handover_code": "", "note": "",
        })
        with self.assertRaises(ValidationError):
            services.advance_order(self.staff, order, "complete_pickup", {
                "delivery_agent_name": "", "delivery_agent_phone": "",
                "handover_code": "000000", "note": "",
            })
        order = services.advance_order(self.staff, order, "complete_pickup", {
            "delivery_agent_name": "", "delivery_agent_phone": "",
            "handover_code": services.handover_code(order), "note": "",
        })
        self.assertEqual(order.status, "picked_up")
        self.assertIsNotNone(order.completed_at)


class MarketInboxTests(MarketFixtures):
    def test_public_enquiry_enters_staff_inbox(self):
        response = self.client.post("/", {
            "name": "Visitor",
            "phone": "0245556677",
            "subject": "Delivery question",
            "message": "Can you deliver this product to my area?",
        })
        self.assertEqual(response.status_code, 302)
        from .models import Conversation
        item = Conversation.objects.get(public_phone="+233245556677")
        self.assertEqual(item.messages.get().sender_type, "visitor")
        self.assertFalse(item.messages.get().read_by_staff)
