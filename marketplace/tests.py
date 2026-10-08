import hashlib
import hmac
import io
import json
import requests
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core.models import Branch, Closing, Company, CustomerReturnRequest, Document, Payment, Product, Stock
from .models import (
    Conversation, ConversationAttachment, CustomerAccount, DeliveryTrackingUpdate,
    MarketListing, MarketListingImage, MarketPaymentAttempt, MarketReturnRequest,
    OnlineOrder, OtpThrottle, RecentView, WishlistItem,
)
from . import services


class MarketFixtures(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Main", code="main", active=True)
        Company.objects.create(
            name="KOFAD IMPEX ENTERPRISE",
            currency="GHS",
            phone="+233241112222",
            secondary_phone="+233242223333",
            email="sales@kofad.example",
            whatsapp_phone="+233243334444",
            address="Dunkwa Offin",
        )
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
    def test_public_home_is_simplified_and_uses_company_contact_settings(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shop the Market")
        self.assertContains(response, "Featured picks")
        self.assertContains(response, "home-hero-v9")
        self.assertContains(response, "Everyday essentials.")
        self.assertContains(response, "Wholesale quantities")
        self.assertNotContains(response, "home-hero-products")
        self.assertNotContains(response, "SHOP BY DEPARTMENT")
        self.assertNotContains(response, "Browse categories.")
        self.assertLessEqual(response.content.count(b'class="market-product-card'), 3)
        self.assertContains(response, "CONTACT US")
        self.assertContains(response, "+233241112222")
        self.assertContains(response, "+233242223333")
        self.assertContains(response, "sales@kofad.example")
        self.assertContains(response, "+233243334444")
        self.assertContains(response, "Sign in to chat with us")
        self.assertNotContains(response, "Everything stays connected.")
        self.assertNotContains(response, "More than a checkout account.")
        self.assertNotContains(response, "Not sure what to order?")
        self.assertNotContains(response, "Send enquiry")
        self.assertNotContains(response, "Live stock · Secure checkout · Tracked fulfilment")
        self.assertNotContains(response, "commerce-global-search")
        self.assertContains(response, 'class="market-contact-link"')
        self.assertNotContains(response, 'class="market-staff-link"')
        self.assertNotContains(response, "Staff login")
        self.assertNotContains(response, "Staff access")
        self.assertEqual(self.client.get("/workspace/").status_code, 302)

    def test_guests_and_customers_can_browse_the_catalog(self):
        guest = self.client.get("/market/")
        self.assertEqual(guest.status_code, 200)
        self.assertContains(guest, 'class="shop-shell-header"')
        self.assertContains(guest, 'aria-label="Search KOFAD Market"')
        self.assertContains(guest, "<h1>All products</h1>", html=True)
        self.assertContains(guest, "/market/access/")

        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        signed_in = self.client.get("/market/")
        self.assertEqual(signed_in.status_code, 200)
        self.assertContains(signed_in, 'class="shop-shell-header"')
        self.assertContains(signed_in, 'aria-label="Search KOFAD Market"')
        self.assertContains(signed_in, "<h1>All products</h1>", html=True)
        self.assertContains(signed_in, "shop-category-strip")
        self.assertContains(signed_in, "shop-product-grid")


    def test_market_search_is_available_before_and_after_sign_in(self):
        guest = self.client.get("/market/")
        self.assertContains(guest, 'aria-label="Search KOFAD Market"')
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        signed_in = self.client.get("/market/")
        self.assertContains(signed_in, 'aria-label="Search KOFAD Market"')

    def test_market_session_state_and_zone_metadata(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        response = self.client.get("/market/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-session-zone="market"')
        self.assertContains(response, 'data-session-status-url="/market/session/state/"')
        state = self.client.get("/market/session/state/")
        self.assertEqual(state.status_code, 200)
        self.assertTrue(state.json()["authenticated"])
        self.assertEqual(state["Cache-Control"], "no-store")
        self.client.post("/market/account/logout/")
        self.assertFalse(self.client.get("/market/session/state/").json()["authenticated"])

    def test_customer_login_replaces_any_staff_identity(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session["market_login_phone"] = self.customer.phone
        session.save()
        response = self.client.post("/market/account/login/", {
            "phone": self.customer.phone,
            "password": "Very-strong-customer-password-42!",
        })
        self.assertRedirects(response, "/market/account/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["market_customer_id"], self.customer.pk)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_login_replaces_any_customer_identity(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        response = self.client.post(settings.LOGIN_URL, {
            "username": self.staff.username,
            "password": "market-owner-password",
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertNotIn("market_customer_id", self.client.session)

    def test_customer_access_page_is_only_the_sign_in_or_create_account_card(self):
        response = self.client.get("/market/access/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in or create account")
        self.assertContains(response, "Enter your mobile number.")
        self.assertContains(response, 'class="market-auth-header"')
        self.assertContains(response, 'class="premium-access-logo"')
        self.assertContains(response, "kofad-official-logo")
        self.assertNotContains(response, 'aria-label="Search KOFAD Market"')
        self.assertNotContains(response, 'class="shop-shell-cart"')
        self.assertNotContains(response, 'class="shop-shell-footer"')
        self.assertNotContains(response, "Sign in with your verified phone number to continue.")
        self.assertNotContains(response, "ONE KOFAD ACCOUNT")
        self.assertNotContains(response, "One number.")
        self.assertNotContains(response, "Existing customer")
        self.assertNotContains(response, "Phone-first identity.")
        self.assertNotContains(response, "KOFAD CHOOSES THE NEXT STEP")

    def test_existing_customer_password_screen_is_plain_and_focused(self):
        session = self.client.session
        session["market_login_phone"] = self.customer.phone
        session.save()
        response = self.client.get("/market/account/login/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in")
        self.assertContains(response, self.customer.phone)
        self.assertNotContains(response, "Your orders are")
        self.assertNotContains(response, "WELCOME BACK")
        self.assertNotContains(response, "Staff member?")

    def test_only_published_products_appear_in_market(self):
        hidden = Product.objects.create(
            name="Internal only part", sku="MKT-002", base_unit="piece",
            pack_name="piece", pack_size=1, cost=1, retail_unit=2, active=True,
        )
        MarketListing.objects.create(
            product=hidden, enabled=False, price_source="retail_unit",
            image_data=b"x", image_thumb=b"x",
        )
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
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
        self.assertIn("no-store", response["Cache-Control"])

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
        self.assertEqual(response.url, "/market/access/")

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

    @override_settings(PAYSTACK_SECRET_KEY="")
    @patch("marketplace.services.requests.post")
    def test_missing_payment_key_does_not_create_a_stuck_attempt(self, post):
        order = self.order()
        before = order.payment_status
        with self.assertRaises(ValidationError):
            services.initialize_paystack(order, "https://example.test/market/payment/return/")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, before)
        self.assertFalse(order.payment_attempts.exists())
        post.assert_not_called()

    @override_settings(PAYSTACK_SECRET_KEY="paystack-secret-for-test")
    @patch("marketplace.services.requests.post")
    def test_invalid_checkout_responses_fail_cleanly(self, post):
        order = self.order()
        for body in (
            [], {"status": True, "data": []},
            {"status": True, "data": {"authorization_url": "https://checkout.paystack.com.evil.test/pay", "access_code": "x"}},
            {"status": True, "data": {"authorization_url": "https://user@checkout.paystack.com/pay", "access_code": "x"}},
        ):
            order = self.order()
            post.return_value = Mock(status_code=200)
            post.return_value.json.return_value = body
            with self.assertRaises(ValidationError):
                services.initialize_paystack(order, "https://example.test/market/payment/return/")
            order.refresh_from_db()
            self.assertEqual(order.payment_status, "pending")
            self.assertEqual(order.payment_attempts.get().status, "submission_unknown")
        self.assertFalse(order.payment_attempts.filter(status="initializing").exists())

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
        response.json.side_effect = lambda: {
            "status": True, "data": {
                "authorization_url": "https://checkout.paystack.com/test-access",
                "access_code": "test-access", "reference": post.call_args.kwargs["json"]["reference"],
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
        self.assertTrue(result.confirmed_reference.startswith("KFD-"))
        self.assertNotEqual(result.confirmed_reference, result.public_reference)
        self.assertEqual(result.customer_reference, result.confirmed_reference)
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
    def test_public_home_no_longer_creates_visitor_enquiries(self):
        before = Conversation.objects.count()
        response = self.client.post("/", {
            "name": "Visitor",
            "phone": "0245556677",
            "subject": "Delivery question",
            "message": "Can you deliver this product to my area?",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Conversation.objects.count(), before)
        self.assertNotContains(response, "Send enquiry")
        self.assertContains(response, "CONTACT US")



class MarketV2CustomerExperienceTests(MarketFixtures):
    def customer_session(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def staff_session(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def test_market_login_sets_two_hour_session_window_and_account_menu(self):
        before = timezone.now().timestamp()
        response = self.client.post("/market/account/login/", {
            "phone": self.customer.phone,
            "password": "Very-strong-customer-password-42!",
        })
        self.assertEqual(response.status_code, 302)
        expires_at = float(self.client.session["market_session_expires_at"])
        self.assertGreaterEqual(expires_at, before + settings.MARKET_SESSION_SECONDS - 2)
        self.assertLessEqual(expires_at, timezone.now().timestamp() + settings.MARKET_SESSION_SECONDS + 2)
        self.assertEqual(settings.MARKET_SESSION_SECONDS, 2 * 60 * 60)
        page = self.client.get("/market/account/")
        self.assertContains(page, "shop-shell-header")
        self.assertContains(page, "shop-account-menu")
        self.assertContains(page, "shop-account-popover")
        self.assertContains(page, "account-v4-signout")
        self.assertContains(page, "Sign out")
        self.assertContains(page, 'action="/market/account/logout/"')
        self.assertNotContains(page, "shop-signout")

    def test_expired_market_session_does_not_expire_staff_identity(self):
        self.staff_session()
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session["market_session_expires_at"] = timezone.now().timestamp() - 1
        session["staff_session_expires_at"] = timezone.now().timestamp() + (12 * 60 * 60)
        session.save()
        response = self.client.get("/market/account/")
        self.assertRedirects(response, "/market/access/", fetch_redirect_response=False)
        self.assertNotIn("market_customer_id", self.client.session)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/workspace/").status_code, 200)

    @patch("marketplace.views.services.send_otp")
    def test_unified_access_does_not_reveal_existing_accounts(self, send_otp):
        send_otp.return_value = "+233241234567"
        response = self.client.post("/market/access/", {"phone": "0241234567"})
        self.assertRedirects(response, "/market/account/verify/", fetch_redirect_response=False)
        send_otp.assert_called_once()
        args, kwargs = send_otp.call_args
        self.assertEqual(args[:2], ("+233241234567", "login"))
        self.assertIs(kwargs["request"], response.wsgi_request)
        self.assertEqual(self.client.session["market_pending_phone"], "+233241234567")
        self.assertNotIn("market_login_phone", self.client.session)

    @patch("marketplace.views.services.send_otp")
    def test_unified_access_uses_same_challenge_for_new_number(self, send_otp):
        send_otp.return_value = "+233245550001"
        response = self.client.post("/market/access/", {"phone": "0245550001"})
        self.assertRedirects(response, "/market/account/verify/", fetch_redirect_response=False)
        args, kwargs = send_otp.call_args
        self.assertEqual(args[:2], ("+233245550001", "login"))
        self.assertIs(kwargs["request"], response.wsgi_request)
        self.assertEqual(self.client.session["market_pending_phone"], "+233245550001")

    def test_customer_account_dashboard_contains_history_summary(self):
        self.customer_session()
        order = self.order()
        response = self.client.get("/market/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your orders")
        self.assertContains(response, "Awaiting payment")
        self.assertContains(response, "Active orders")
        self.assertContains(response, "market-mobile-dock")

    def test_customer_checkout_and_paid_order_hide_payment_provider(self):
        self.customer_session()
        session = self.client.session
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()
        checkout = self.client.get("/market/checkout/")
        self.assertEqual(checkout.status_code, 200)
        self.assertNotContains(checkout, "Paystack")
        self.assertContains(checkout, "Make payment")

        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(
            order=order, reference="KFD-PRIVATE-PAYMENT", amount=order.total,
            currency="GHS", status="pending",
        )
        order = services.finalize_payment(attempt.reference, {
            "status": "success",
            "amount": int(order.total * 100),
            "currency": "GHS",
            "channel": "mobile_money",
        })
        response = self.client.get(f"/market/orders/{order.pk}/")
        self.assertContains(response, order.confirmed_reference)
        self.assertNotContains(response, "Paystack")
        self.assertNotContains(response, attempt.reference)
        self.assertNotContains(response, "mobile_money")

    @override_settings(PAYSTACK_SECRET_KEY="sk_test_example")
    @patch("marketplace.views.services.initialize_paystack")
    def test_checkout_make_payment_uses_same_origin_handoff(self, initialize_payment):
        self.customer_session()
        session = self.client.session
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()
        initialize_payment.return_value = Mock(
            authorization_url="https://checkout.paystack.com/test-checkout"
        )
        response = self.client.post("/market/checkout/", {
            "fulfilment": "pickup",
            "recipient_name": self.customer.full_name,
            "phone": self.customer.phone,
            "email": self.customer.email,
            "delivery_zone": "",
            "region": "",
            "town": "",
            "address_line": "",
            "landmark": "",
            "ghana_post_gps": "",
            "latitude": "",
            "longitude": "",
            "customer_note": "",
        })
        order = OnlineOrder.objects.latest("created_at")
        self.assertRedirects(
            response,
            f"/market/orders/{order.pk}/payment/launch/",
            fetch_redirect_response=False,
        )
        initialize_payment.assert_called_once()
        self.assertEqual(initialize_payment.call_args.args[0].pk, order.pk)
        self.assertEqual(self.client.session["market_cart"], {})

    def test_payment_launch_page_has_automatic_and_manual_hubtel_handoff(self):
        self.customer_session()
        order = self.order()
        MarketPaymentAttempt.objects.create(
            order=order, provider="hubtel", reference="launch-ref",
            amount=order.total, currency="GHS", status="pending",
            authorization_url="https://pay.hubtel.com/test-checkout",
        )
        response = self.client.get(f"/market/orders/{order.pk}/payment/launch/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "payment-launch.")
        self.assertContains(response, "https://pay.hubtel.com/test-checkout")
        self.assertContains(response, "Continue to secure payment")
        self.assertIn("no-store", response["Cache-Control"])

    def test_payment_launch_blocks_untrusted_checkout_host(self):
        self.customer_session()
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(
            order=order, provider="hubtel", reference="bad-launch-ref",
            amount=order.total, currency="GHS", status="pending",
            authorization_url="https://pay.hubtel.com.evil.example/checkout",
        )
        response = self.client.get(f"/market/orders/{order.pk}/payment/launch/")
        self.assertRedirects(response, f"/market/orders/{order.pk}/", fetch_redirect_response=False)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, "attention")

    @patch("marketplace.services._google_route")
    def test_distance_delivery_price_is_proportional_and_saved_on_order(self, google_route):
        company = Company.objects.get()
        company.delivery_pricing_mode = "distance"
        company.delivery_rate_per_km = Decimal("1.00")
        company.delivery_minimum_fee = Decimal("0")
        company.delivery_max_distance_km = Decimal("50")
        company.delivery_origin_latitude = Decimal("5.603700")
        company.delivery_origin_longitude = Decimal("-0.186900")
        company.delivery_origin_label = "KOFAD Main Shop"
        company.save()
        google_route.return_value = {
            "distance_km": Decimal("0.10"),
            "duration_seconds": 90,
            "polyline": "test-route",
            "source": "google_route",
        }

        quote = services.delivery_quote(Decimal("5.604000"), Decimal("-0.187000"))
        self.assertEqual(quote["fee"], Decimal("0.10"))
        self.assertEqual(quote["distance_km"], Decimal("0.10"))

        order = services.create_order(
            self.customer,
            {str(self.listing.pk): 2},
            {
                "fulfilment": "delivery",
                "recipient_name": self.customer.full_name,
                "phone": self.customer.phone,
                "email": self.customer.email,
                "delivery_zone": None,
                "region": "Greater Accra",
                "town": "Accra",
                "address_line": "Pinned delivery point",
                "landmark": "",
                "ghana_post_gps": "",
                "latitude": Decimal("5.604000"),
                "longitude": Decimal("-0.187000"),
                "customer_note": "",
            },
        )
        self.assertEqual(order.delivery_fee, Decimal("0.10"))
        self.assertEqual(order.total, Decimal("200.10"))
        self.assertEqual(order.delivery_distance_km, Decimal("0.10"))
        self.assertEqual(order.delivery_distance_source, "google_route")
        self.assertEqual(order.delivery_route_polyline, "test-route")
        self.assertEqual(order.delivery_origin_latitude, Decimal("5.603700"))

    def test_delivery_quote_endpoint_and_checkout_show_map_picker(self):
        self.customer_session()
        session = self.client.session
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()
        response = self.client.get("/market/checkout/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-location-map")
        self.assertContains(response, "Use my current location")
        self.assertContains(response, "Search location")
        quote = self.client.get("/market/delivery/quote/?lat=5.60&lng=-0.18")
        self.assertEqual(quote.status_code, 200)
        self.assertEqual(quote.json()["fee"], "0.00")

    def test_map_pages_use_policy_compliant_osm_headers_and_host(self):
        self.customer_session()
        session = self.client.session
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()
        response = self.client.get("/market/checkout/")
        self.assertEqual(
            response["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
        csp = response["Content-Security-Policy"]
        self.assertIn("https://tile.openstreetmap.org", csp)
        self.assertNotIn("https://*.tile.openstreetmap.org", csp)
        map_js = (
            settings.BASE_DIR
            / "marketplace/static/marketplace/market-map.js"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            map_js,
        )
        self.assertNotIn("{s}.tile.openstreetmap.org", map_js)

    @override_settings(GOOGLE_MAPS_BROWSER_KEY="browser-google-key", GOOGLE_MAPS_MAP_ID="map-id-123", GOOGLE_MAPS_BROWSER_KEY_RESTRICTED=True)
    def test_checkout_can_activate_google_maps_browser_experience(self):
        self.customer_session()
        session = self.client.session
        session["market_cart"] = {str(self.listing.pk): 1}
        session.save()
        response = self.client.get("/market/checkout/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-google-maps-key="browser-google-key"')
        self.assertContains(response, 'data-google-map-id="map-id-123"')
        self.assertContains(response, "Google Maps + Places")
        csp = response["Content-Security-Policy"]
        self.assertIn("https://*.googleapis.com", csp)
        self.assertIn("https://*.gstatic.com", csp)

    @override_settings(GOOGLE_MAPS_SERVER_KEY="test-google-key")
    @patch("marketplace.services.requests.get")
    def test_location_search_uses_google_when_configured(self, get):
        self.customer_session()
        response = Mock()
        response.ok = True
        response.json.return_value = {
            "status": "OK",
            "results": [{
                "formatted_address": "Osu, Accra, Ghana",
                "geometry": {"location": {"lat": 5.556, "lng": -0.182}},
            }],
        }
        get.return_value = response
        result = self.client.get("/market/location/search/?q=Osu%20Accra")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["results"][0]["label"], "Osu, Accra, Ghana")

    def test_delivery_policy_settings_save_company_origin_and_rate(self):
        self.staff_session()
        response = self.client.post("/market-settings/", {
            "action": "policy",
            "delivery_enabled": "on",
            "delivery_pricing_mode": "distance",
            "delivery_flat_fee": "0",
            "delivery_rate_per_km": "1.00",
            "delivery_minimum_fee": "0",
            "delivery_max_distance_km": "100",
            "delivery_origin_label": "KOFAD Main Shop",
            "delivery_origin_latitude": "5.603717",
            "delivery_origin_longitude": "-0.186964",
        })
        self.assertRedirects(response, "/market-settings/", fetch_redirect_response=False)
        company = Company.objects.get()
        self.assertEqual(company.delivery_pricing_mode, "distance")
        self.assertEqual(company.delivery_rate_per_km, Decimal("1.00"))
        self.assertEqual(company.delivery_origin_latitude, Decimal("5.603717"))

    def test_staff_order_page_shows_customer_delivery_map(self):
        order = services.create_order(
            self.customer,
            {str(self.listing.pk): 1},
            {
                "fulfilment": "delivery",
                "recipient_name": self.customer.full_name,
                "phone": self.customer.phone,
                "email": self.customer.email,
                "delivery_zone": None,
                "region": "Greater Accra",
                "town": "Accra",
                "address_line": "Pinned delivery point",
                "landmark": "",
                "ghana_post_gps": "",
                "latitude": Decimal("5.603717"),
                "longitude": Decimal("-0.186964"),
                "customer_note": "",
            },
        )
        self.staff_session()
        response = self.client.get(f"/online-orders/{order.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DELIVERY MAP")
        self.assertContains(response, "data-location-map")
        self.assertContains(response, "5.603717")

    def test_market_search_uses_customer_facing_tags(self):
        self.customer_session()
        self.listing.tags = "hydraulic excavator service filter maintenance"
        self.listing.save(update_fields=["tags"])
        response = self.client.get("/market/?q=maintenance")
        self.assertContains(response, self.listing.display_name)

    def test_curated_external_photo_renders_when_no_uploaded_photo_exists(self):
        self.listing.image_data = None
        self.listing.image_thumb = None
        self.listing.image_url = "https://images.unsplash.com/photo-test?auto=format"
        self.listing.image_credit = "Unsplash · Test"
        self.listing.save(update_fields=["image_data", "image_thumb", "image_url", "image_credit"])
        response = self.client.get("/market/")
        self.assertContains(response, self.listing.image_url)
        self.assertIn("https://images.unsplash.com", response["Content-Security-Policy"])

    def test_customer_can_change_password_with_current_password(self):
        self.customer_session()
        response = self.client.post("/market/account/security/", {
            "current_password": "Very-strong-customer-password-42!",
            "password": "Stronger-new-customer-password-643!",
            "password_confirm": "Stronger-new-customer-password-643!",
        })
        self.assertRedirects(response, "/market/account/", fetch_redirect_response=False)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.check_password("Stronger-new-customer-password-643!"))

    def test_online_commerce_datasets_appear_in_export_center(self):
        self.staff_session()
        response = self.client.get("/exports/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Online orders &amp; fulfilment")
        self.assertContains(response, "Market customer accounts")
        self.assertContains(response, "Published Market catalog")
        self.assertContains(response, "Customer support conversations")


class MarketV2SupportTests(MarketFixtures):
    def customer_session(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def staff_session(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def test_customer_support_accepts_and_hashes_document_attachment(self):
        self.customer_session()
        upload = SimpleUploadedFile(
            "concern.txt", b"Serial number and issue details", content_type="text/plain"
        )
        response = self.client.post("/market/messages/", {
            "subject": "Product concern",
            "message": "Please review the attached details.",
            "attachment": upload,
        })
        thread = Conversation.objects.get(customer=self.customer)
        self.assertRedirects(
            response, f"/market/messages/{thread.pk}/", fetch_redirect_response=False
        )
        attachment = ConversationAttachment.objects.get(message__conversation=thread)
        self.assertEqual(attachment.original_name, "concern.txt")
        self.assertEqual(
            attachment.sha256,
            hashlib.sha256(b"Serial number and issue details").hexdigest(),
        )

    def test_support_attachment_is_private_to_thread_participants(self):
        self.customer_session()
        thread = Conversation.objects.create(
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Private support",
        )
        message = thread.messages.create(
            sender_type="customer", body="Evidence", read_by_customer=True
        )
        attachment = ConversationAttachment.objects.create(
            message=message,
            original_name="evidence.txt",
            mime_type="text/plain",
            size=8,
            sha256=hashlib.sha256(b"evidence").hexdigest(),
            data=b"evidence",
        )
        response = self.client.get(f"/market/support/attachments/{attachment.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"evidence")
        self.client.post("/market/account/logout/")
        denied = self.client.get(f"/market/support/attachments/{attachment.pk}/")
        self.assertEqual(denied.status_code, 404)

    def test_live_support_updates_return_only_new_messages(self):
        self.customer_session()
        thread = Conversation.objects.create(
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Live support",
        )
        first = thread.messages.create(
            sender_type="customer", body="First", read_by_customer=True
        )
        second = thread.messages.create(
            sender_type="staff", body="Second", read_by_staff=True
        )
        response = self.client.get(
            f"/market/support/conversations/{thread.pk}/updates/?after={first.pk}"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row["id"] for row in payload["messages"]], [second.pk])
        second.refresh_from_db()
        self.assertTrue(second.read_by_customer)

    def test_staff_accepts_chat_before_reply_and_customer_sees_worker_name(self):
        thread = Conversation.objects.create(
            branch=self.branch,
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Need help",
        )
        thread.messages.create(
            sender_type="customer", body="Can somebody help me?", read_by_customer=True
        )
        self.staff.first_name = "Ama"
        self.staff.last_name = "Support"
        self.staff.save(update_fields=["first_name", "last_name"])
        self.staff_session()

        blocked = self.client.post(
            f"/online-inbox/{thread.pk}/",
            {"message": "Reply before accept", "action": "reply"},
        )
        self.assertEqual(blocked.status_code, 302)
        self.assertFalse(thread.messages.filter(sender_type="staff").exists())

        accepted = self.client.post(
            f"/online-inbox/{thread.pk}/", {"action": "accept"}
        )
        self.assertEqual(accepted.status_code, 302)
        thread.refresh_from_db()
        self.assertEqual(thread.assigned_to, self.staff)
        self.assertIsNotNone(thread.accepted_at)

        self.client.logout()
        self.customer_session()
        customer_page = self.client.get(f"/market/messages/{thread.pk}/")
        self.assertContains(customer_page, "Ama Support")
        self.assertContains(customer_page, "has connected with you")
        state = self.client.get(
            f"/market/support/conversations/{thread.pk}/updates/?after=0"
        ).json()
        self.assertEqual(state["agent"], "Ama Support")
        self.assertTrue(state["agent_connected"])

    def test_assigned_worker_can_leave_chat_back_to_waiting_queue(self):
        thread = Conversation.objects.create(
            branch=self.branch,
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Queue test",
            assigned_to=self.staff,
            accepted_at=timezone.now(),
        )
        thread.messages.create(
            sender_type="customer", body="Waiting", read_by_customer=True
        )
        self.staff_session()
        response = self.client.post(
            f"/online-inbox/{thread.pk}/", {"action": "leave"}
        )
        self.assertRedirects(response, "/online-inbox/", fetch_redirect_response=False)
        thread.refresh_from_db()
        self.assertIsNone(thread.assigned_to)
        self.assertIsNone(thread.accepted_at)

    def test_closing_chat_clears_messages_and_attachments(self):
        thread = Conversation.objects.create(
            branch=self.branch,
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Private chat",
            assigned_to=self.staff,
            accepted_at=timezone.now(),
        )
        message = thread.messages.create(
            sender_type="customer", body="Sensitive details", read_by_customer=True
        )
        ConversationAttachment.objects.create(
            message=message,
            original_name="private.txt",
            mime_type="text/plain",
            size=7,
            sha256=hashlib.sha256(b"private").hexdigest(),
            data=b"private",
        )
        self.staff_session()
        response = self.client.post(
            f"/online-inbox/{thread.pk}/", {"action": "close"}
        )
        self.assertEqual(response.status_code, 302)
        thread.refresh_from_db()
        self.assertEqual(thread.status, "closed")
        self.assertEqual(thread.closed_reason, "staff_closed")
        self.assertIsNotNone(thread.closed_at)
        self.assertEqual(thread.messages.count(), 0)
        self.assertEqual(
            ConversationAttachment.objects.filter(message__conversation=thread).count(), 0
        )

    def test_staff_reply_without_customer_response_auto_closes_after_24_hours(self):
        thread = Conversation.objects.create(
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Stale support",
            assigned_to=self.staff,
            accepted_at=timezone.now() - timedelta(hours=26),
        )
        reply = thread.messages.create(
            sender_type="staff", staff=self.staff, body="Are you still there?", read_by_staff=True
        )
        stale_at = timezone.now() - timedelta(hours=25)
        thread.messages.filter(pk=reply.pk).update(created_at=stale_at)
        Conversation.objects.filter(pk=thread.pk).update(updated_at=stale_at)
        self.customer_session()
        response = self.client.get(f"/market/messages/{thread.pk}/")
        self.assertEqual(response.status_code, 200)
        thread.refresh_from_db()
        self.assertEqual(thread.status, "closed")
        self.assertEqual(thread.closed_reason, "customer_inactive")
        self.assertEqual(thread.messages.count(), 0)
        self.assertContains(response, "closed automatically after 24 hours")

    def test_image_support_attachment_is_normalized_to_webp(self):
        image = Image.new("RGB", (1400, 900), (15, 80, 120))
        source = io.BytesIO()
        image.save(source, "JPEG")
        upload = SimpleUploadedFile(
            "problem.jpg", source.getvalue(), content_type="image/jpeg"
        )
        payload = services.prepare_support_attachment(upload)
        self.assertEqual(payload["mime_type"], "image/webp")
        self.assertTrue(payload["original_name"].endswith(".webp"))
        self.assertLessEqual(payload["size"], 10 * 1024 * 1024)



class MarketV3CommerceTests(MarketFixtures):
    def customer_session(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def staff_session(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def paid_completed_order(self):
        order = self.order()
        reference = "KFD-V3-PAID"
        MarketPaymentAttempt.objects.create(
            order=order, reference=reference, amount=order.total,
            currency="GHS", status="pending",
        )
        order = services.finalize_payment(reference, {
            "status": "success",
            "amount": int(order.total * 100),
            "currency": "GHS",
            "channel": "mobile_money",
        })
        order = services.advance_order(self.staff, order, "prepare", {
            "delivery_agent_name": "", "delivery_agent_phone": "",
            "handover_code": "", "note": "",
        })
        order = services.advance_order(self.staff, order, "ready_pickup", {
            "delivery_agent_name": "", "delivery_agent_phone": "",
            "handover_code": "", "note": "",
        })
        order = services.advance_order(self.staff, order, "complete_pickup", {
            "delivery_agent_name": "", "delivery_agent_phone": "",
            "handover_code": services.handover_code(order), "note": "",
        })
        return order

    def test_wishlist_toggle_and_reorder_restore_customer_shopping_intent(self):
        self.customer_session()
        response = self.client.post(
            f"/market/wishlist/{self.listing.pk}/toggle/",
            {"next": "/market/"},
        )
        self.assertRedirects(response, "/market/", fetch_redirect_response=False)
        self.assertTrue(WishlistItem.objects.filter(
            customer=self.customer, listing=self.listing
        ).exists())

        order = self.order()
        response = self.client.post(f"/market/orders/{order.pk}/reorder/")
        self.assertRedirects(response, "/market/cart/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["market_cart"][str(self.listing.pk)], 2)

    def test_product_views_build_recent_history_and_search_suggestions(self):
        self.customer_session()
        self.client.get(f"/market/products/{self.listing.pk}/")
        self.client.get(f"/market/products/{self.listing.pk}/")
        recent = RecentView.objects.get(customer=self.customer, listing=self.listing)
        self.assertEqual(recent.view_count, 2)

        self.listing.tags = "excavator hydraulic maintenance"
        self.listing.save(update_fields=["tags"])
        response = self.client.get("/market/search/suggestions/?q=hydraulic")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["id"], self.listing.pk)

    def test_gallery_photos_are_compressed_and_render_on_product_page(self):
        image = Image.new("RGB", (1800, 1100), (30, 100, 145))
        source = io.BytesIO()
        image.save(source, "JPEG")
        upload = SimpleUploadedFile("detail.jpg", source.getvalue(), content_type="image/jpeg")
        photo = services.save_gallery_image(self.listing, upload, alt_text="Filter side view")
        self.assertTrue(photo.image_data)
        self.assertTrue(photo.image_thumb)
        self.assertEqual(photo.image_mime, "image/webp")
        response = self.client.get(f"/market/products/{self.listing.pk}/")
        self.assertContains(response, f"/market/gallery/{photo.pk}/image/thumb/")

    @override_settings(PAYSTACK_SECRET_KEY="sk_test_refund", SMS_ENABLED=False)
    @patch("marketplace.services.requests.post")
    def test_online_return_posts_kofad_return_then_exact_paystack_refund(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {
            "status": True,
            "message": "Refund has been queued for processing",
            "data": {
                "id": 3018284,
                "status": "pending",
                "amount": 10000,
                "currency": "GHS",
            },
        }
        order = self.paid_completed_order()
        line = order.lines.get(product=self.product)
        self.assertIsNotNone(line.sale_line_id)
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 18)

        item = services.create_market_return_request(
            self.customer,
            order,
            [{"line": line.pk, "quantity": 1, "condition": "sellable"}],
            "The item does not match the required specification.",
            "refund",
        )
        self.assertEqual(item.refund_amount, Decimal("100.00"))
        services.review_market_return_request(self.staff, item, "approve", "Eligible return.")
        services.review_market_return_request(self.staff, item, "process", "Physical item received.")
        item.refresh_from_db()
        self.assertEqual(item.status, "processing")
        self.assertEqual(item.provider_refund_id, "3018284")
        self.assertEqual(item.provider_refund_status, "pending")
        self.assertIsNotNone(item.core_return_request_id)
        core_return = CustomerReturnRequest.objects.get(pk=item.core_return_request_id)
        self.assertEqual(core_return.status, "approved")
        self.assertEqual(Stock.objects.get(branch=self.branch, product=self.product).quantity, 19)

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["transaction"], order.payment_reference)
        self.assertEqual(payload["amount"], 10000)
        self.assertEqual(payload["currency"], "GHS")

        services.apply_paystack_refund_webhook("refund.processed", {
            "id": 3018284,
            "status": "processed",
            "amount": 10000,
            "currency": "GHS",
        })
        item.refresh_from_db()
        self.assertEqual(item.status, "completed")
        self.assertEqual(item.provider_refund_status, "processed")
        self.assertIsNotNone(item.refund_processed_at)
        event_count = order.events.filter(status="refund_processed").count()
        services.apply_paystack_refund_webhook("refund.processed", {
            "id": 3018284,
            "status": "processed",
            "amount": 10000,
            "currency": "GHS",
        })
        self.assertEqual(order.events.filter(status="refund_processed").count(), event_count)

    @override_settings(PAYSTACK_SECRET_KEY="sk_test_refund", SMS_ENABLED=False)
    @patch("marketplace.services.requests.post")
    def test_unknown_refund_submission_outcome_cannot_be_blindly_retried(self, post):
        post.side_effect = requests.Timeout("connection lost after submission")
        order = self.paid_completed_order()
        line = order.lines.get(product=self.product)
        item = services.create_market_return_request(
            self.customer,
            order,
            [{"line": line.pk, "quantity": 1, "condition": "sellable"}],
            "The item is faulty and needs to be returned safely.",
            "refund",
        )
        services.review_market_return_request(self.staff, item, "approve", "Eligible.")
        services.review_market_return_request(self.staff, item, "process", "Item received.")
        item.refresh_from_db()
        self.assertEqual(item.status, "refund_attention")
        self.assertEqual(item.provider_refund_status, "submission_unknown")
        self.assertIsNotNone(item.refund_initiated_at)
        self.assertEqual(post.call_count, 1)

        services.review_market_return_request(self.staff, item, "sync_refund", "")
        item.refresh_from_db()
        self.assertEqual(item.status, "refund_attention")
        self.assertEqual(item.provider_refund_status, "submission_unknown")
        self.assertEqual(post.call_count, 1)

    def test_delivery_tracking_keeps_driver_eta_and_customer_visible_evidence(self):
        order = self.order(fulfilment="delivery")
        eta = timezone.now() + timedelta(hours=2)
        update = services.save_delivery_tracking(self.staff, order, {
            "delivery_agent_name": "Kojo Driver",
            "delivery_agent_phone": "+233241111111",
            "estimated_delivery_at": eta,
            "status": "Driver assigned",
            "note": "Your order is being loaded for delivery.",
            "latitude": Decimal("5.603717"),
            "longitude": Decimal("-0.186964"),
            "customer_visible": True,
        })
        order.refresh_from_db()
        self.assertEqual(order.delivery_agent_name, "Kojo Driver")
        self.assertEqual(update.status, "Driver assigned")
        self.assertTrue(order.events.filter(status="delivery_update").exists())

    def test_market_intelligence_and_v3_exports_are_staff_accessible(self):
        self.staff_session()
        response = self.client.get("/market-analytics/?days=30")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Market Intelligence")
        export = self.client.get("/exports/")
        self.assertContains(export, "Online return requests")
        self.assertContains(export, "Online delivery tracking")


class MarketV3LiveSupportTests(MarketFixtures):
    def customer_session(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def test_typing_presence_is_visible_to_other_side(self):
        self.customer_session()
        thread = Conversation.objects.create(
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Typing test",
        )
        response = self.client.post(
            f"/market/support/conversations/{thread.pk}/typing/"
        )
        self.assertEqual(response.status_code, 200)
        thread.refresh_from_db()
        self.assertIsNotNone(thread.customer_typing_at)

        self.client.post("/market/account/logout/")
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()
        response = self.client.get(
            f"/market/support/conversations/{thread.pk}/updates/?after=0"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["other_typing"])


class ProductMarketVisibilityTests(MarketFixtures):
    def staff_session(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def test_unpublished_product_renders_market_configuration_collapsed(self):
        self.staff_session()
        self.listing.enabled = False
        self.listing.save(update_fields=["enabled"])
        response = self.client.get(f"/products/{self.product.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-market-details hidden')
        self.assertContains(response, 'data-market-offline-hint')
        self.assertContains(response, 'data-market-preview-link hidden')

    def test_published_product_renders_market_configuration_open(self):
        self.staff_session()
        response = self.client.get(f"/products/{self.product.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-market-details')
        self.assertNotContains(response, 'data-market-details hidden')
        self.assertNotContains(response, 'data-market-preview-link hidden')

    def test_unpublished_product_cannot_open_gallery_uploader(self):
        self.staff_session()
        self.listing.enabled = False
        self.listing.save(update_fields=["enabled"])
        response = self.client.get(f"/market-catalog/{self.listing.pk}/gallery/")
        self.assertRedirects(
            response,
            f"/products/{self.product.pk}/",
            fetch_redirect_response=False,
        )


class CustomerOtpProviderTests(MarketFixtures):
    @override_settings(
        CUSTOMER_OTP_ENABLED=True,
        SMS_ENABLED=True,
        ARKESEL_API_KEY="sms-api-key-for-test",
        SMS_PROVIDER="arkesel",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("marketplace.services.secrets.randbelow", return_value=123456)
    @patch("marketplace.services.get_provider")
    def test_customer_otp_uses_regular_live_sms_and_stores_only_digest(
        self, get_provider_mock, _randbelow
    ):
        provider = get_provider_mock.return_value
        provider.submit.return_value = Mock(
            status="accepted", error_code="", error_detail=""
        )

        phone = services.send_otp("+233245550001", "register")
        self.assertEqual(phone, "+233245550001")
        provider.validate.assert_called_once()
        args = provider.submit.call_args.args
        self.assertEqual(args[0], "+233245550001")
        self.assertIn("123456", args[1])
        self.assertEqual(args[2], "KOFAD")
        self.assertFalse(args[4])

        row = OtpThrottle.objects.get(phone=phone, purpose="register")
        self.assertNotEqual(row.code_digest, "123456")
        self.assertEqual(len(row.code_digest), 64)
        self.assertIsNone(row.verified_at)

    @override_settings(
        CUSTOMER_OTP_ENABLED=True,
        SMS_ENABLED=True,
        ARKESEL_API_KEY="sms-api-key-for-test",
        SMS_PROVIDER="arkesel",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("marketplace.services.secrets.randbelow", return_value=123456)
    @patch("marketplace.services.get_provider")
    def test_locally_generated_otp_verifies_without_second_provider_call(
        self, get_provider_mock, _randbelow
    ):
        provider = get_provider_mock.return_value
        provider.submit.return_value = Mock(
            status="accepted", error_code="", error_detail=""
        )
        services.send_otp("+233245550002", "register")

        verified = services.verify_otp("+233245550002", "123456", "register")
        self.assertEqual(verified, "+233245550002")
        self.assertEqual(provider.submit.call_count, 1)
        row = OtpThrottle.objects.get(phone=verified, purpose="register")
        self.assertIsNotNone(row.verified_at)
        self.assertEqual(row.code_digest, "")

    @override_settings(
        CUSTOMER_OTP_ENABLED=True,
        SMS_ENABLED=True,
        ARKESEL_API_KEY="sms-api-key-for-test",
        SMS_PROVIDER="arkesel",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("marketplace.services.get_provider")
    def test_sms_gateway_rejection_does_not_create_a_usable_otp(self, get_provider_mock):
        provider = get_provider_mock.return_value
        provider.submit.return_value = Mock(
            status="failed",
            error_code="provider_rejected",
            error_detail="Insufficient balance",
        )
        with self.assertRaisesMessage(
            ValidationError,
            "We could not send the verification code right now. Please try again.",
        ):
            services.send_otp("+233245550003", "register")
        row = OtpThrottle.objects.get(phone="+233245550003", purpose="register")
        self.assertEqual(row.code_digest, "")
        self.assertIsNone(row.last_sent_at)

    @override_settings(
        CUSTOMER_OTP_ENABLED=True,
        SMS_ENABLED=True,
        ARKESEL_API_KEY="sms-api-key-for-test",
        SMS_PROVIDER="arkesel",
        SMS_SENDER_ID="KOFAD",
    )
    @patch("marketplace.services.secrets.randbelow", return_value=654321)
    @patch("marketplace.services.get_provider")
    def test_wrong_otp_is_throttled_and_cannot_verify(
        self, get_provider_mock, _randbelow
    ):
        provider = get_provider_mock.return_value
        provider.submit.return_value = Mock(
            status="accepted", error_code="", error_detail=""
        )
        phone = services.send_otp("+233245550005", "register")
        with self.assertRaisesMessage(ValidationError, "That verification code is not correct."):
            services.verify_otp(phone, "111111", "register")
        row = OtpThrottle.objects.get(phone=phone, purpose="register")
        self.assertEqual(row.attempts, 1)
        self.assertIsNone(row.verified_at)


class CustomerPhoneOnboardingTests(MarketFixtures):
    @patch("marketplace.views.services.send_otp")
    def test_existing_number_uses_same_private_phone_challenge(self, send_otp):
        send_otp.return_value = "+233241234567"
        response = self.client.post("/market/access/", {"phone": "0241234567"})
        self.assertRedirects(response, "/market/account/verify/", fetch_redirect_response=False)
        args, kwargs = send_otp.call_args
        self.assertEqual(args[:2], ("+233241234567", "login"))
        self.assertIs(kwargs["request"], response.wsgi_request)
        self.assertEqual(self.client.session["market_pending_phone"], "+233241234567")
        self.assertNotIn("market_login_phone", self.client.session)

    @patch("marketplace.views.services.send_otp")
    def test_new_number_moves_to_otp_then_name_and_password(self, send_otp):
        send_otp.return_value = "+233245550004"
        response = self.client.post("/market/access/", {"phone": "0245550004"})
        self.assertRedirects(response, "/market/account/verify/", fetch_redirect_response=False)

        session = self.client.session
        session["market_verified_phone"] = "+233245550004"
        session.save()
        response = self.client.get("/market/account/finish/")
        self.assertContains(response, "Full name")
        self.assertContains(response, "Create password")
        self.assertNotContains(response, "Email address")

        response = self.client.post("/market/account/finish/", {
            "full_name": "New Market Customer",
            "password": "Strong-new-market-password-842!",
            "password_confirm": "Strong-new-market-password-842!",
        })
        self.assertRedirects(response, "/market/", fetch_redirect_response=False)
        customer = CustomerAccount.objects.get(phone="+233245550004")
        self.assertEqual(customer.full_name, "New Market Customer")
        self.assertTrue(customer.check_password("Strong-new-market-password-842!"))
        self.assertEqual(self.client.session["market_customer_id"], customer.pk)


class MarketCatalogScaleAndDeletionTests(MarketFixtures):
    def staff_session(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()

    def customer_session(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def test_customer_market_paginates_beyond_old_catalog_cutoff(self):
        products = [
            Product(
                name=f"Scale product {index:03d}",
                sku=f"SCALE-{index:03d}",
                category="Scale test",
                base_unit="piece",
                pack_name="piece",
                pack_size=1,
                cost=Decimal("1.00"),
                retail_unit=Decimal("2.00"),
                active=True,
            )
            for index in range(225)
        ]
        Product.objects.bulk_create(products)
        created = list(Product.objects.filter(sku__startswith="SCALE-").order_by("sku"))
        MarketListing.objects.bulk_create([
            MarketListing(
                product=product,
                enabled=True,
                title=product.name,
                description="Published scale-test product.",
                price_source="retail_unit",
                image_data=b"x",
                image_thumb=b"x",
            )
            for product in created
        ])
        self.customer_session()
        first = self.client.get("/market/")
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "226 products in this view")
        self.assertContains(first, "Page 1")
        last_page = self.client.get("/market/?page=5")
        self.assertEqual(last_page.status_code, 200)
        self.assertContains(last_page, "Scale product 224")

    def test_catalog_studio_can_publish_hide_and_feature_ready_listing(self):
        self.staff_session()
        self.listing.enabled = False
        self.listing.featured = False
        self.listing.save(update_fields=["enabled", "featured"])

        response = self.client.post("/market-catalog/", {
            "action": "publish",
            "product": self.product.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.listing.refresh_from_db()
        self.assertTrue(self.listing.enabled)

        self.client.post("/market-catalog/", {"action": "feature", "product": self.product.pk})
        self.listing.refresh_from_db()
        self.assertTrue(self.listing.featured)

        self.client.post("/market-catalog/", {"action": "hide", "product": self.product.pk})
        self.listing.refresh_from_db()
        self.assertFalse(self.listing.enabled)
        self.assertFalse(self.listing.featured)

    def test_manager_can_delete_closed_customer_inbox_record_after_content_is_cleared(self):
        self.staff_session()
        thread = Conversation.objects.create(
            branch=self.branch,
            customer=self.customer,
            public_name=self.customer.full_name,
            public_phone=self.customer.phone,
            subject="Closed support thread",
            status="closed",
            closed_at=timezone.now(),
            closed_reason="staff_closed",
        )
        response = self.client.post(f"/online-inbox/{thread.pk}/", {"action": "delete"})
        self.assertRedirects(response, "/online-inbox/", fetch_redirect_response=False)
        self.assertFalse(Conversation.objects.filter(pk=thread.pk).exists())


class MarketOtpReliabilityTests(MarketFixtures):
    def test_verify_otp_accepts_code_pasted_with_spacing(self):
        phone = "+233245551111"
        code = "123456"
        OtpThrottle.objects.create(
            phone=phone,
            purpose="reset",
            expires_at=timezone.now() + timedelta(minutes=10),
            code_digest=services._otp_digest(phone, "reset", code),
        )
        self.assertEqual(services.verify_otp(phone, "123 456", "reset"), phone)
        row = OtpThrottle.objects.get(phone=phone, purpose="reset")
        self.assertIsNotNone(row.verified_at)
        self.assertEqual(row.code_digest, "")

    @patch("marketplace.views.services.verify_otp")
    def test_duplicate_reset_verification_redirects_to_finish_without_reusing_code(self, verify_otp):
        session = self.client.session
        session["market_reset_phone"] = self.customer.phone
        session["market_reset_verified_phone"] = self.customer.phone
        session.save()
        response = self.client.post("/market/account/password-reset/verify/", {"code": "123456"})
        self.assertRedirects(
            response,
            "/market/account/password-reset/finish/",
            fetch_redirect_response=False,
        )
        verify_otp.assert_not_called()

    @patch("marketplace.views.services.verify_otp")
    def test_duplicate_registration_verification_redirects_to_finish_without_reusing_code(self, verify_otp):
        phone = "+233245552222"
        session = self.client.session
        session["market_pending_phone"] = phone
        session["market_verified_phone"] = phone
        session.save()
        response = self.client.post("/market/account/verify/", {"code": "123456"})
        self.assertRedirects(response, "/market/account/finish/", fetch_redirect_response=False)
        verify_otp.assert_not_called()
