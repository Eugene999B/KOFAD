import hashlib
import json
import os
import uuid
from datetime import timedelta
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import Access, Message, MessageTemplate, SmsAttempt, SmsEvent
from core.tests import Fixtures
from core.sms.providers import Arkesel, Submission, get_provider
from core.sms.service import create_draft,queue_message,process_one,receive_callback,recover_stale,estimate,normalize_phone
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

    def test_draft_key_and_queue_duplicate_protection(self):
        one = create_draft(self.user,self.branch,self.customer,"Hello",source_key="receipt:1")
        two = create_draft(self.user,self.branch,self.customer,"Hello",source_key="receipt:1")
        self.assertEqual(one.pk,two.pk)
        queue_message(self.user,self.branch,one.pk)
        with self.assertRaises(ValidationError):
            queue_message(self.user,self.branch,one.pk)

    def test_permission_and_consent(self):
        viewer = User.objects.create_user("viewer",password="viewer-password-long")
        viewer.access.branches.add(self.branch)
        message = self.draft()
        with self.assertRaises(PermissionDenied):
            queue_message(viewer,self.branch,message.pk)
        self.customer.consent = False
        self.customer.save()
        with self.assertRaises(ValidationError):
            queue_message(self.user,self.branch,message.pk)

    def test_disabled_configuration_leaves_draft(self):
        message = self.draft()
        with override_settings(SMS_ENABLED=False),self.assertRaises(ValidationError):
            queue_message(self.user,self.branch,message.pk)
        message.refresh_from_db()
        self.assertEqual(message.status,"draft")

    @patch("core.sms.providers.Arkesel.submit",return_value=Submission("accepted","provider-123",200))
    def test_worker_acceptance_is_not_delivery(self,submit):
        message = self.draft()
        queue_message(self.user,self.branch,message.pk)
        self.assertTrue(process_one())
        self.assertFalse(process_one())
        message.refresh_from_db()
        self.assertEqual(message.status,"accepted")
        self.assertEqual(submit.call_count,1)
        self.assertEqual(message.delivery_attempts.count(),1)
        callback = submit.call_args.args[3]
        self.assertIn("/sms/callback/",callback)
        self.assertIn("token=",callback)

    @patch("core.sms.providers.Arkesel.submit",return_value=Submission("unknown",error_code="uncertain_network_result"))
    def test_unknown_never_auto_retries_or_fails_over(self,submit):
        message = self.draft()
        queue_message(self.user,self.branch,message.pk)
        process_one()
        self.assertFalse(process_one())
        with self.assertRaises(ValidationError):
            queue_message(self.user,self.branch,message.pk,retry=True)
        self.assertEqual(submit.call_count,1)

    @patch("core.sms.providers.Arkesel.submit",return_value=Submission("retry_wait",http_status=429))
    def test_throttled_attempt_retries_later(self,submit):
        message = self.draft()
        queue_message(self.user,self.branch,message.pk)
        process_one()
        message.refresh_from_db()
        self.assertEqual(message.status,"retry_wait")
        self.assertFalse(process_one())
        Message.objects.filter(pk=message.pk).update(next_attempt_at=timezone.now()-timedelta(seconds=1))
        process_one()
        self.assertEqual(submit.call_count,2)

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
        self.assertFalse(process_one())

    def test_templates_reject_attribute_and_format_access(self):
        for body in ("{customer.password}","{total:03}","{missing}","{company"):
            with self.assertRaises(ValidationError):
                validate_template(body)
        validate_template("{company}: {reference}")

    def test_registry_rejects_unknown_provider(self):
        with self.assertRaises(ValidationError):
            get_provider("uninstalled")

    @patch("core.sms.providers.urlopen")
    def test_arkesel_contract_and_no_false_success(self,urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = json.dumps({"status":"success","data":[{"id":"sms-001"}]}).encode()
        result = Arkesel().submit("+233241234567","Hello","KOFAD","https://kofad.example/callback",False)
        self.assertEqual(result.status,"accepted")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url,"https://sms.arkesel.com/api/v2/sms/send")
        payload = json.loads(request.data)
        self.assertEqual(payload["recipients"],["233241234567"])
        self.assertFalse(payload["sandbox"])
        response.read.return_value = b"not json"
        self.assertEqual(Arkesel().submit("+233241234567","Hello","KOFAD","https://kofad.example/callback",False).status,"unknown")

    @patch("core.sms.providers.urlopen",side_effect=URLError("timed out"))
    def test_transport_error_is_unknown(self,_):
        self.assertEqual(Arkesel().submit("+233241234567","Hello","KOFAD","https://kofad.example/callback",False).status,"unknown")


class InitialAdminTests(TestCase):
    @patch.dict(os.environ,{"KOFAD_INITIAL_ADMIN_PASSWORD":"ADMIN"})
    def test_initial_admin_direct_login_and_optional_password_change(self):
        call_command("bootstrap_admin",confirm_initial_setup=True)
        user = User.objects.get(username="ADMIN")
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("ADMIN"))
        self.assertFalse(user.access.must_change_password)
        self.client.post("/login/",{"username":"ADMIN","password":"ADMIN"})
        for path in ("/","/admin/","/sales/new/","/communications/"):
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

    @patch("core.sms.providers.Arkesel.submit",return_value=Submission("accepted","sms-concurrent",200))
    def test_two_workers_submit_once(self,submit):
        message = create_draft(self.user,self.branch,self.customer,"Single submission")
        queue_message(self.user,self.branch,message.pk)
        def worker(_):
            close_old_connections()
            try:
                return process_one()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(worker,range(2)))
        self.assertCountEqual(outcomes,[True,False])
        self.assertEqual(submit.call_count,1)
        self.assertEqual(SmsAttempt.objects.filter(message=message).count(),1)


@override_settings(**SETTINGS)
class SmsEvidenceTests(Fixtures,TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone,self.customer.consent = "+233241234567",True
        self.customer.save()

    def test_callback_before_submission_response_keeps_delivered(self):
        from urllib.parse import urlsplit,parse_qs
        message = create_draft(self.user,self.branch,self.customer,"Callback race test")
        queue_message(self.user,self.branch,message.pk)
        def callback_first(recipient,body,sender,callback,sandbox):
            url = urlsplit(callback)
            attempt_id = uuid.UUID(url.path.strip("/").split("/")[-1])
            self.assertTrue(receive_callback(attempt_id,parse_qs(url.query)["token"][0],"fast-1","DELIVERED"))
            return Submission("accepted","fast-1",200)
        with patch("core.sms.providers.Arkesel.submit",side_effect=callback_first):
            process_one()
        message.refresh_from_db()
        self.assertEqual(message.status,"delivered")

    def test_queued_content_cannot_be_rewritten(self):
        from django.db import DatabaseError,transaction
        message = create_draft(self.user,self.branch,self.customer,"Original SMS")
        queue_message(self.user,self.branch,message.pk)
        with self.assertRaises(DatabaseError),transaction.atomic():
            Message.objects.filter(pk=message.pk).update(body="Changed after approval")

    def test_consent_revoked_after_queue_blocks_network(self):
        message = create_draft(self.user,self.branch,self.customer,"Consent test")
        queue_message(self.user,self.branch,message.pk)
        self.customer.consent = False
        self.customer.save()
        with patch("core.sms.providers.Arkesel.submit") as submit:
            process_one()
            submit.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status,"failed")


@override_settings(**SETTINGS)
class ReminderTests(Fixtures,TestCase):
    def setUp(self):
        self.setup_data()
        self.customer.phone,self.customer.consent = "+233241234567",True
        self.customer.save()
        from core.sms.templates import DEFAULTS
        for code,(name,body) in DEFAULTS.items():
            MessageTemplate.objects.get_or_create(code=code,defaults={"name":name,"body":body})

    def test_paid_invoice_stops_queued_reminder(self):
        from core.sms.templates import render_for_document
        from core import services
        invoice = self.sale(payments=[],party=self.customer.pk,due_date=timezone.localdate().isoformat())
        message = create_draft(self.user,self.branch,self.customer,render_for_document(invoice,"debt"),
            source_key=f"debt:{invoice.pk}:50:{timezone.localdate()}")
        queue_message(self.user,self.branch,message.pk)
        services.post_payment(self.user,self.branch,{"invoice":str(invoice.pk),"amount":"50","method":"cash"},uuid.uuid4())
        with patch("core.sms.providers.Arkesel.submit") as submit:
            process_one()
            submit.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.status,"failed")
