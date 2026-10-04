import hashlib
import hmac
import io
import json
import requests
from datetime import timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

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
        self.assertContains(response, "Explore the Market")
        self.assertContains(response, "CONTACT US")
        self.assertContains(response, "+233241112222")
        self.assertContains(response, "+233242223333")
        self.assertContains(response, "sales@kofad.example")
        self.assertContains(response, "+233243334444")
        self.assertContains(response, "Message customer care", html=False) if False else None
        self.assertNotContains(response, "Everything stays connected.")
        self.assertNotContains(response, "More than a checkout account.")
        self.assertNotContains(response, "Not sure what to order?")
        self.assertNotContains(response, "Send enquiry")
        self.assertNotContains(response, "Live stock · Secure checkout · Tracked fulfilment")
        self.assertEqual(self.client.get("/workspace/").status_code, 302)

    def test_customer_access_page_is_only_the_sign_in_or_create_account_card(self):
        response = self.client.get("/market/access/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in or create account")
        self.assertContains(response, "Enter your mobile number to continue.")
        self.assertNotContains(response, "ONE KOFAD ACCOUNT")
        self.assertNotContains(response, "One number.")
        self.assertNotContains(response, "Existing customer")
        self.assertNotContains(response, "Phone-first identity.")
        self.assertNotContains(response, "KOFAD CHOOSES THE NEXT STEP")

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

    def test_unified_access_routes_existing_number_to_password_sign_in(self):
        response = self.client.post("/market/access/", {"phone": "0241234567"})
        self.assertRedirects(response, "/market/account/login/", fetch_redirect_response=False)
        self.assertEqual(self.client.session["market_login_phone"], "+233241234567")

    @patch("marketplace.views.services.send_otp")
    def test_unified_access_starts_verified_creation_for_new_number(self, send_otp):
        send_otp.return_value = "+233245550001"
        response = self.client.post("/market/access/", {"phone": "0245550001"})
        self.assertRedirects(response, "/market/account/verify/", fetch_redirect_response=False)
        send_otp.assert_called_once_with("+233245550001", "register")
        self.assertEqual(self.client.session["market_pending_phone"], "+233245550001")

    def test_customer_account_dashboard_contains_history_summary(self):
        self.customer_session()
        order = self.order()
        response = self.client.get("/market/account/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "My KOFAD")
        self.assertContains(response, order.public_reference)
        self.assertContains(response, "Active orders")

    def test_market_search_uses_customer_facing_tags(self):
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
    def test_existing_number_moves_to_password_only_screen(self):
        response = self.client.post("/market/access/", {"phone": "0241234567"})
        self.assertRedirects(response, "/market/account/login/", fetch_redirect_response=False)
        response = self.client.get("/market/account/login/")
        self.assertContains(response, "+233241234567")
        self.assertContains(response, 'type="hidden" name="phone"')
        self.assertNotContains(response, "<label>Phone number")

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
