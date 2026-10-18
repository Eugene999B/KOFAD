from unittest.mock import Mock, patch
import requests
from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, override_settings
from django.utils import timezone
from .tests import MarketFixtures
from .models import Conversation, MarketListingImage, MarketPaymentAttempt, MarketReturnAttachment, MarketReturnRequest
from . import services, views
from .paystack_reconciliation import reconcile_due
from core.models import Branch


@override_settings(PAYSTACK_SECRET_KEY="test-key")
class CheckoutSecurityTests(MarketFixtures):
    @patch("marketplace.services.requests.post", side_effect=requests.Timeout)
    def test_unknown_checkout_cannot_create_second_attempt(self, post):
        order = self.order()
        for _ in range(2):
            with self.assertRaises(ValidationError):
                services.initialize_paystack(order, "https://market.kofadimpex.com/market/payment/return/")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(order.payment_attempts.count(), 1)
        self.assertEqual(order.payment_attempts.get().status, "submission_unknown")

    @patch("marketplace.services.requests.post")
    def test_existing_hubtel_attempt_cannot_switch_to_paystack(self, post):
        order = self.order()
        MarketPaymentAttempt.objects.create(order=order, provider="hubtel", reference="hubtel-active",
                                            amount=order.total, status="pending")
        with self.assertRaises(ValidationError):
            services.initialize_paystack(order, "https://market.kofadimpex.com/market/payment/return/")
        post.assert_not_called()

    @patch("marketplace.services.requests.get")
    def test_background_paystack_verification_posts_once(self, get):
        order = self.order()
        attempt = MarketPaymentAttempt.objects.create(order=order, provider="paystack",
            reference="test-background", amount=order.total, currency="GHS", status="pending",
            next_check_at=timezone.now())
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {"status": True, "data": {
            "reference": attempt.reference, "status": "success",
            "amount": int(order.total * 100), "currency": "GHS", "channel": "mobile_money",
        }}
        self.assertEqual(reconcile_due(), 1)
        self.assertEqual(reconcile_due(), 0)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertIsNotNone(order.sale_document_id)
        self.assertEqual(get.call_count, 1)

    def test_inactive_products_never_serve_public_listing_media(self):
        image = MarketListingImage.objects.create(
            listing=self.listing, image_data=b"gallery", image_thumb=b"thumbnail",
        )
        primary_url = f"/market/products/{self.listing.pk}/image/thumb/"
        gallery_url = f"/market/gallery/{image.pk}/image/thumb/"
        for url in (primary_url, gallery_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

        self.product.active = False
        self.product.save(update_fields=["active"])
        # The listing remains enabled but is no longer a public product.
        for url in (primary_url, gallery_url):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_unicode_signature_fails_closed(self):
        self.assertFalse(services.paystack_signature_valid(b"{}", "é" * 128))

    @override_settings(ALLOWED_HOSTS=["testserver", "staff.kofadimpex.com", "market.kofadimpex.com"])
    def test_return_attachment_requires_staff_location_or_matching_customer(self):
        order = self.order()
        item = MarketReturnRequest.objects.create(
            order=order, customer=self.customer, reason="Private return evidence",
        )
        attachment = MarketReturnAttachment.objects.create(
            request=item, original_name="private-evidence.png",
            mime_type="image/png", size=4, sha256="test-digest", data=b"test",
        )
        url = f"/market/returns/attachments/{attachment.pk}/"
        other_branch = Branch.objects.create(name="Other returns location", code="oth-ret")
        staff = User.objects.create_user("limited-return-staff", password="test-password-only")
        staff.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        staff.access.branches.add(other_branch)
        staff.access.refresh_from_db()
        self.client.force_login(staff)
        session = self.client.session
        session["access_version"] = staff.access.session_version
        session.save()
        response = self.client.get(url, HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(response.status_code, 404)

        staff.access.branches.add(self.branch)
        staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = staff.access.session_version
        session.save()
        response = self.client.get(url, HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"test")

        self.client.logout()
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        response = self.client.get(url, HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"test")

    def test_staff_cannot_read_other_branch_support_order(self):
        order = self.order()
        other = Branch.objects.create(name="Other", code="other")
        staff = User.objects.create_user("restricted-staff", password="long-test-password")
        staff.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        staff.access.branches.add(other)
        conversation = Conversation.objects.create(order=order, customer=self.customer)
        request = RequestFactory().get("/")
        request.user, request.session = staff, {}
        self.assertEqual(views._conversation_access(request, conversation), "")

    def test_staff_cannot_read_orderless_support_from_other_branch(self):
        other = Branch.objects.create(name="Other support", code="oth-sup")
        staff = User.objects.create_user("other-support-staff", password="long-test-password")
        staff.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        staff.access.branches.add(other)
        conversation = Conversation.objects.create(branch=self.branch, customer=self.customer)
        request = RequestFactory().get("/")
        request.user, request.session = staff, {}
        self.assertEqual(views._conversation_access(request, conversation), "")

    def test_staff_post_cannot_reply_private_details_to_unverified_external_contact(self):
        conversation = Conversation.objects.create(
            branch=self.branch,
            public_name="External WhatsApp contact",
            public_phone="+233551234567",
            subject="Unverified external support",
            assigned_to=self.staff,
        )
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()
        response = self.client.post(
            f"/online-inbox/{conversation.pk}/",
            {"action": "reply", "message": "Private payment information"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(conversation.messages.filter(sender_type="staff").exists())

    def test_support_documents_reject_active_or_legacy_binary_content(self):
        with self.assertRaisesMessage(ValidationError, "Active or embedded PDF"):
            services.prepare_support_attachment(SimpleUploadedFile(
                "evidence.pdf",
                b"%PDF-1.7\n1 0 obj<</JavaScript 2 0 R>>endobj\n%%EOF",
                content_type="application/pdf",
            ))
        with self.assertRaisesMessage(ValidationError, "plain PDF, DOCX, XLSX"):
            services.prepare_support_attachment(SimpleUploadedFile(
                "legacy.doc", b"legacy-binary-office", content_type="application/msword"
            ))

    def test_bot_dashboard_requires_management_permission(self):
        staff = User.objects.create_user("cashier-only", password="long-test-password")
        staff.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        staff.access.branches.add(self.branch)
        self.client.force_login(staff)
        staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()
        self.assertEqual(self.client.get("/settings/whatsapp-bot/").status_code, 403)

    def test_owner_can_open_bot_activation_dashboard(self):
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session["branch"] = self.branch.pk
        session.save()
        response = self.client.get("/settings/whatsapp-bot/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Waiting for activation")
        self.assertContains(response, "Customer support inbox")
