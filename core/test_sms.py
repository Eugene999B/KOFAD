import hashlib
import json
import os
import uuid
import requests
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Access, Message, MessageTemplate, SmsAttempt, SmsEvent
from core.tests import Fixtures
from core.sms.providers import Arkesel, Submission, get_provider
from core.sms.service import create_draft,send_message_now,receive_callback,recover_stale,estimate,normalize_phone
from core.sms.templates import validate_template

SETTINGS = dict(SMS_ENABLED=True,SMS_PROVIDER="arkesel",SMS_SENDER_ID="KOFAD",SMS_SANDBOX=False,
                SMS_PUBLIC_ORIGIN="https://kofad.example",ARKESEL_API_KEY="test-only-key")


@override_settings(**SETTINGS)
class SmsTests(Fixtures,TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone = "+233241234567"
        self.customer.consent = True
        self.customer.save()

    def draft(self):
        return create_draft(self.user,self.branch,self.customer,"KOFAD: thank you for your purchase.")

    def test_normalization_and_encoding(self):
        self.assertEqual(normalize_phone("024 123 4567"),"+233241234567")
        for value in ("hello241234567","+233241234567;0241111111","0123"):
            with self.assertRaises(ValidationError):
                normalize_phone(value)
        self.assertEqual(estimate("a"*160),("gsm7",1))
        self.assertEqual(estimate("^"*81),("gsm7",2))
        self.assertEqual(estimate("😀"*36),("utf16",2))
        self.assertEqual(estimate("界"*70),("utf16",1))

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_draft_key_and_direct_send_duplicate_protection(self, submit_many):
        submit_many.return_value = [
            Submission("accepted", "provider-1", 200, recipient="+233241234567")
        ]
        one = create_draft(self.user,self.branch,self.customer,"Hello",source_key="receipt:1")
        two = create_draft(self.user,self.branch,self.customer,"Hello",source_key="receipt:1")
        self.assertEqual(one.pk,two.pk)
        send_message_now(self.user,self.branch,one.pk)
        with self.assertRaises(ValidationError):
            send_message_now(self.user,self.branch,one.pk)
        self.assertEqual(submit_many.call_count, 1)

    def test_permission_and_consent(self):
        viewer = User.objects.create_user("viewer",password="viewer-password-long")
        viewer.access.branches.add(self.branch)
        message = self.draft()
        with self.assertRaises(PermissionDenied):
            send_message_now(viewer,self.branch,message.pk)
        self.customer.consent = False
        self.customer.save()
        with self.assertRaises(ValidationError):
            send_message_now(self.user,self.branch,message.pk)

    def test_disabled_configuration_leaves_draft(self):
        message = self.draft()
        with override_settings(SMS_ENABLED=False),self.assertRaises(ValidationError):
            send_message_now(self.user,self.branch,message.pk)
        message.refresh_from_db()
        self.assertEqual(message.status,"draft")

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_cashier_sale_receipt_sms_does_not_require_general_messaging_permission(self, submit_many):
        submit_many.return_value = [
            Submission("accepted", "sale-receipt-provider-id", 200, recipient="+233241234567")
        ]
        cashier = User.objects.create_user("receipt-cashier", password="cashier-password-long")
        cashier.user_permissions.add(Permission.objects.get(codename="operate_sales"))
        cashier.access.branches.add(self.branch)
        self.assertFalse(cashier.has_perm("core.send_messages"))
        self.customer.consent = False
        self.customer.save(update_fields=["consent"])
        self.authenticate_client(cashier)

        response = self.client.post(
            "/api/trades/",
            data=json.dumps({
                "kind": "sale",
                "items": [{"product": self.product.pk, "mode": "retail_unit", "quantity": 1}],
                "party": self.customer.pk,
                "customer_consent": True,
                "payments": [{"method": "cash", "amount": "50"}],
            }),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid.uuid4()),
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertTrue(payload["sms_requested"])
        self.assertTrue(payload["can_send_sms"])
        self.assertEqual(payload["sms_status"], "accepted")
        self.assertEqual(submit_many.call_count, 1)
        self.customer.refresh_from_db()
        self.assertTrue(self.customer.consent)

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_direct_acceptance_is_sent_not_delivery(self, submit_many):
        submit_many.return_value = [
            Submission("accepted", "provider-123", 200, recipient="+233241234567")
        ]
        message = self.draft()
        sent = send_message_now(self.user,self.branch,message.pk)
        self.assertEqual(sent.status,"accepted")
        self.assertEqual(submit_many.call_count,1)
        self.assertEqual(sent.delivery_attempts.count(),1)
        self.assertIn("/sms/delivery/", submit_many.call_args.args[3])
        self.assertIn("token=", submit_many.call_args.args[3])

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_unknown_never_auto_retries_or_fails_over(self, submit_many):
        submit_many.return_value = [
            Submission(
                "unknown",
                error_code="uncertain_network_result",
                error_detail="Provider result is unknown.",
                recipient="+233241234567",
            )
        ]
        message = self.draft()
        sent = send_message_now(self.user,self.branch,message.pk)
        self.assertEqual(sent.status, "unknown")
        with self.assertRaises(ValidationError):
            send_message_now(self.user,self.branch,message.pk,retry=True)
        self.assertEqual(submit_many.call_count,1)

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_throttled_direct_send_fails_safely_and_requires_manual_retry(self, submit_many):
        submit_many.side_effect = [
            [Submission(
                "retry_wait",
                http_status=429,
                error_code="rate_limited",
                error_detail="Arkesel rate limit reached.",
                recipient="+233241234567",
            )],
            [Submission("accepted", "provider-after-retry", 200, recipient="+233241234567")],
        ]
        message = self.draft()
        sent = send_message_now(self.user,self.branch,message.pk)
        self.assertEqual(sent.status,"failed")
        self.assertIn("rate limit", sent.last_error.lower())
        retried = send_message_now(self.user,self.branch,message.pk,retry=True)
        self.assertEqual(retried.status, "accepted")
        self.assertEqual(submit_many.call_count,2)

    def test_authenticated_callbacks_are_idempotent_and_monotonic(self):
        message = self.draft()
        message.status,message.attempts,message.sandbox = "sending",1,False
        message.save()
        token = "unguessable-test-token"
        attempt = SmsAttempt.objects.create(message=message,number=1,provider="arkesel",
            callback_digest=hashlib.sha256(token.encode()).hexdigest())
        self.assertFalse(receive_callback(attempt.pk,"wrong","sms-1","DELIVERED"))
        self.assertTrue(receive_callback(attempt.pk,token,"sms-1","DELIVERED"))
        self.assertTrue(receive_callback(attempt.pk,token,"sms-1","DELIVERED"))
        self.assertTrue(receive_callback(attempt.pk,token,"sms-1","QUEUED"))
        self.assertFalse(receive_callback(attempt.pk,token,"different-id","DELIVERED"))
        message.refresh_from_db()
        self.assertEqual(message.status,"delivered")
        self.assertEqual(SmsEvent.objects.count(),2)

    def test_sandbox_callback_cannot_claim_live_delivery(self):
        message = self.draft()
        message.status,message.attempts,message.sandbox = "sending",1,True
        message.save()
        token = "sandbox-token"
        attempt = SmsAttempt.objects.create(message=message,number=1,provider="arkesel",
            callback_digest=hashlib.sha256(token.encode()).hexdigest())
        receive_callback(attempt.pk,token,"sandbox-1","DELIVERED")
        message.refresh_from_db()
        self.assertEqual(message.status,"simulated")

    def test_worker_crash_becomes_unknown_not_resend(self):
        message = self.draft()
        message.status,message.attempts = "sending",1
        message.save()
        attempt = SmsAttempt.objects.create(message=message,number=1,provider="arkesel",callback_digest="a"*64)
        SmsAttempt.objects.filter(pk=attempt.pk).update(started_at=timezone.now()-timedelta(minutes=10))
        self.assertEqual(recover_stale(),1)
        message.refresh_from_db()
        self.assertEqual(message.status,"unknown")

    def test_templates_reject_attribute_and_format_access(self):
        for body in ("{customer.password}","{total:03}","{missing}","{company"):
            with self.assertRaises(ValidationError):
                validate_template(body)
        validate_template("{company}: {reference}")

    def test_registry_rejects_unknown_provider(self):
        with self.assertRaises(ValidationError):
            get_provider("uninstalled")

    @patch("core.sms.providers.requests.post")
    def test_arkesel_contract_and_no_false_success(self,post):
        response = post.return_value
        response.status_code = 200
        response.json.return_value = {"status":"success","data":[{"id":"sms-001"}]}
        response.text = '{"status":"success"}'
        result = Arkesel().submit("+233241234567","Hello","KOFAD","https://kofad.example/callback",False)
        self.assertEqual(result.status,"accepted")
        self.assertEqual(post.call_args.args[0],"https://sms.arkesel.com/api/v2/sms/send")
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"]["recipients"],["233241234567"])
        self.assertNotIn("sandbox", kwargs["json"])
        self.assertEqual(kwargs["headers"]["api-key"],"test-only-key")
        self.assertEqual(kwargs["headers"]["User-Agent"],"KOFAD-IMPEX/1.0")
        self.assertFalse(kwargs["allow_redirects"])
        response.json.side_effect = ValueError("not json")
        response.text = "not json"
        self.assertEqual(Arkesel().submit("+233241234567","Hello","KOFAD","https://kofad.example/callback",False).status,"unknown")

    @patch("core.sms.providers.requests.post",side_effect=requests.RequestException("timed out"))
    def test_transport_error_is_unknown(self,_):
        self.assertEqual(Arkesel().submit("+233241234567","Hello","KOFAD","https://kofad.example/callback",False).status,"unknown")

    @patch("core.sms.providers.requests.post")
    def test_arkesel_security_edge_block_is_reported_as_provider_failure(self,post):
        response = post.return_value
        response.status_code = 403
        response.json.side_effect = ValueError("html")
        response.text = "The site owner has blocked access based on your browser's signature."
        result = Arkesel().submit("+233241234567","Hello","KOFAD","https://kofad.example/callback",False)
        self.assertEqual(result.status,"failed")
        self.assertIn("security edge", result.error_detail)


class InitialAdminTests(TestCase):
    @patch.dict(os.environ,{"KOFAD_INITIAL_ADMIN_PASSWORD":"ADMIN"})
    def test_initial_admin_direct_login_and_optional_password_change(self):
        call_command("bootstrap_admin",confirm_initial_setup=True)
        user = User.objects.get(username="ADMIN")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("ADMIN"))
        self.assertFalse(user.access.must_change_password)
        self.client.post("/login/",{"username":"ADMIN","password":"ADMIN"})
        for path in ("/","/administration/","/sales/new/","/communications/"):
            self.assertEqual(self.client.get(path).status_code,200)
        result = self.client.post("/account/password/",{"old_password":"ADMIN",
            "new_password1":"A-unique-counter-password-1948!","new_password2":"A-unique-counter-password-1948!"})
        self.assertEqual(result.status_code,302)
        user.refresh_from_db()
        self.assertFalse(user.check_password("ADMIN"))
        self.assertFalse(user.access.must_change_password)
        self.assertEqual(self.client.get("/").status_code,200)
        with self.assertRaises(CommandError):
            call_command("bootstrap_admin",confirm_initial_setup=True)
        user.refresh_from_db()
        self.assertFalse(user.check_password("ADMIN"))

    def test_admin_bootstrap_requires_explicit_invocation(self):
        with self.assertRaises(CommandError):
            call_command("bootstrap_admin")
        self.assertFalse(User.objects.filter(username="ADMIN").exists())


from concurrent.futures import ThreadPoolExecutor
from django.db import connections, close_old_connections
from django.test import TransactionTestCase


@override_settings(**SETTINGS)
class SmsConcurrencyTests(Fixtures,TransactionTestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone,self.customer.consent = "+233241234567",True
        self.customer.save()

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_two_direct_senders_submit_once(self, submit_many):
        submit_many.return_value = [
            Submission("accepted", "sms-concurrent", 200, recipient="+233241234567")
        ]
        message = create_draft(self.user,self.branch,self.customer,"Single submission")

        def sender(_):
            close_old_connections()
            try:
                user = User.objects.get(pk=self.user.pk)
                branch = type(self.branch).objects.get(pk=self.branch.pk)
                try:
                    send_message_now(user, branch, message.pk)
                    return "sent"
                except ValidationError:
                    return "blocked"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(sender,range(2)))
        self.assertCountEqual(outcomes,["sent","blocked"])
        self.assertEqual(submit_many.call_count,1)
        self.assertEqual(SmsAttempt.objects.filter(message=message).count(),1)


@override_settings(**SETTINGS)
class SmsEvidenceTests(Fixtures,TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone,self.customer.consent = "+233241234567",True
        self.customer.save()

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_provider_acceptance_then_delivery_callback_is_monotonic(self, submit_many):
        submit_many.return_value = [
            Submission("accepted", "fast-1", 200, recipient="+233241234567")
        ]
        message = create_draft(self.user,self.branch,self.customer,"Callback delivery test")
        send_message_now(self.user,self.branch,message.pk)
        message.refresh_from_db()
        self.assertEqual(message.status, "accepted")
        from core.sms.service import receive_delivery_callback, _delivery_callback_token
        self.assertTrue(receive_delivery_callback(_delivery_callback_token(), "fast-1", "DELIVERED"))
        self.assertTrue(receive_delivery_callback(_delivery_callback_token(), "fast-1", "QUEUED"))
        message.refresh_from_db()
        self.assertEqual(message.status,"delivered")

    @patch("core.sms.providers.Arkesel.submit_many")
    def test_sent_content_cannot_be_rewritten(self, submit_many):
        from django.db import DatabaseError,transaction
        submit_many.return_value = [
            Submission("accepted", "immutable-1", 200, recipient="+233241234567")
        ]
        message = create_draft(self.user,self.branch,self.customer,"Original SMS")
        send_message_now(self.user,self.branch,message.pk)
        with self.assertRaises(DatabaseError),transaction.atomic():
            Message.objects.filter(pk=message.pk).update(body="Changed after approval")

    def test_consent_revoked_before_direct_send_blocks_network(self):
        message = create_draft(self.user,self.branch,self.customer,"Consent test")
        self.customer.consent = False
        self.customer.save()
        with patch("core.sms.providers.Arkesel.submit_many") as submit:
            with self.assertRaises(ValidationError):
                send_message_now(self.user,self.branch,message.pk)
            submit.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status,"draft")


@override_settings(**SETTINGS)
class ReminderTests(Fixtures,TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone,self.customer.consent = "+233241234567",True
        self.customer.save()
        from core.sms.templates import DEFAULTS
        for code,(name,body) in DEFAULTS.items():
            MessageTemplate.objects.get_or_create(code=code,defaults={"name":name,"body":body})

    def test_paid_invoice_stops_stale_reminder_before_direct_send(self):
        from core.sms.templates import render_for_document
        from core import services
        invoice = self.sale(payments=[],party=self.customer.pk,due_date=timezone.localdate().isoformat())
        message = create_draft(self.user,self.branch,self.customer,render_for_document(invoice,"debt"),
            source_key=f"debt:{invoice.pk}:50:{timezone.localdate()}")
        services.post_payment(self.user,self.branch,{"invoice":str(invoice.pk),"amount":"50","method":"cash"},uuid.uuid4())
        with patch("core.sms.providers.Arkesel.submit_many") as submit:
            with self.assertRaises(ValidationError):
                send_message_now(self.user,self.branch,message.pk)
            submit.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status,"draft")
