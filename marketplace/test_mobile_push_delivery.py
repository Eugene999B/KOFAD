"""Secure FCM service notification tests; every HTTP provider call is mocked."""
import hashlib
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.tests import Fixtures
from marketplace import services
from marketplace.mobile_auth_models import MobileDeviceSession
from marketplace.mobile_push import _cipher
from marketplace.mobile_push_models import MobilePushOutbox, MobilePushSubscription
from marketplace.mobile_push_delivery import queue_order_push,process_due_push,_send,delivery_ready
from marketplace.models import CustomerAccount


@override_settings(
    KOFAD_NATIVE_AUTH_ENABLED=True,KOFAD_NATIVE_PUSH_ENABLED=True,
    KOFAD_NATIVE_PUSH_DELIVERY_ENABLED=True,KOFAD_FCM_PROJECT_ID="kofad-test-project",
    KOFAD_FCM_SERVICE_ACCOUNT_B64="configured-in-private-env-placeholder",
)
class NativePushDeliveryTests(Fixtures,TestCase):
    def setUp(self):
        self.setup_data()
        self.customer=CustomerAccount.objects.create(
            phone="0241399000",full_name="Push tester",active=True,password_hash="test-hash")
        self.session=MobileDeviceSession.objects.create(
            channel="customer",customer=self.customer,
            customer_credential_stamp=services.customer_credential_stamp(self.customer),
            access_hash=hashlib.sha256(b"A"*43).hexdigest(),
            refresh_hash=hashlib.sha256(b"R"*43).hexdigest(),
            access_expires_at=timezone.now()+timedelta(minutes=15),
            refresh_expires_at=timezone.now()+timedelta(days=3),
        )
        self.token="TEST_fcm_device_token_really_long_1234567"
        self.subscription=MobilePushSubscription.objects.create(
            device_session=self.session,
            token_digest=hashlib.sha256(self.token.encode()).hexdigest(),
            encrypted_token=_cipher().encrypt(self.token.encode()).decode(),
            service_opt_in=True,marketing_opt_in=False,
        )
        self.order=SimpleNamespace(pk="42424242-4242-4242-4242-424242424242",customer_id=self.customer.pk)

    def test_queue_deduplicates_repeated_order_events_without_sending(self):
        with patch("marketplace.mobile_push_delivery.delivery_ready",return_value=True):
            self.assertEqual(queue_order_push(self.order,"paid"),1)
            self.assertEqual(queue_order_push(self.order,"paid"),0)
            self.assertEqual(queue_order_push(self.order,"preparing"),1)
        self.assertEqual(MobilePushOutbox.objects.count(),2)
        for row in MobilePushOutbox.objects.all():
            self.assertNotIn("42424242",row.body)
            self.assertNotIn(self.customer.full_name,row.body)
            self.assertNotIn(self.customer.phone,row.body)
            self.assertEqual(row.kind,"service")

    def test_sends_exactly_once_via_existing_worker_and_is_bounded(self):
        with patch("marketplace.mobile_push_delivery.delivery_ready",return_value=True):
            queue_order_push(self.order,"paid")
            with patch("marketplace.mobile_push_delivery._send",return_value="sent") as sender:
                self.assertEqual(process_due_push(limit=1),1)
                self.assertEqual(process_due_push(limit=1),0)
                sender.assert_called_once()
        self.assertEqual(MobilePushOutbox.objects.get().status,"sent")

    def test_unknown_network_outcome_never_blindly_retries(self):
        with patch("marketplace.mobile_push_delivery.delivery_ready",return_value=True):
            queue_order_push(self.order,"paid")
            with patch("marketplace.mobile_push_delivery._send",side_effect=TimeoutError()):
                self.assertEqual(process_due_push(limit=10),1)
            self.assertEqual(process_due_push(limit=10),0)
        self.assertEqual(MobilePushOutbox.objects.get().status,"uncertain")

    def test_opt_out_and_revocation_prevent_device_delivery(self):
        with patch("marketplace.mobile_push_delivery.delivery_ready",return_value=True):
            queue_order_push(self.order,"paid")
            self.subscription.service_opt_in=False
            self.subscription.save(update_fields=["service_opt_in"])
            with patch("marketplace.mobile_push_delivery._send") as sender:
                process_due_push()
                sender.assert_not_called()
            self.assertEqual(MobilePushOutbox.objects.get().status,"failed")
            self.subscription.service_opt_in=True
            self.subscription.save(update_fields=["service_opt_in"])
            queue_order_push(self.order,"preparing")
            self.session.revoked_at=timezone.now()
            self.session.save(update_fields=["revoked_at"])
            with patch("marketplace.mobile_push_delivery._send") as sender:
                process_due_push()
                sender.assert_not_called()
        self.assertEqual(MobilePushOutbox.objects.filter(status="failed").count(),2)

    def test_unregistered_devices_are_removed_without_sending_twice(self):
        with patch("marketplace.mobile_push_delivery.delivery_ready",return_value=True):
            queue_order_push(self.order,"paid")
            with patch("marketplace.mobile_push_delivery._send",return_value="unregistered"):
                self.assertEqual(process_due_push(),1)
        self.assertFalse(MobilePushSubscription.objects.exists())
        self.assertFalse(MobilePushOutbox.objects.exists())

    def test_provider_request_contains_only_generic_notification(self):
        ok=Mock(status_code=200)
        ok.json.return_value={"name":"projects/kofad-test-project/messages/abc"}
        with patch("marketplace.mobile_push_delivery.delivery_ready",return_value=True), \
             patch("marketplace.mobile_push_delivery._access_token",return_value="test_access_token"), \
             patch("marketplace.mobile_push_delivery.requests.post",return_value=ok) as http:
            self.assertEqual(_send(self.subscription,"KOFAD Market order update",
                                    "Your order has an update."),"sent")
        args,kw=http.call_args
        self.assertEqual(args[0],"https://fcm.googleapis.com/v1/projects/kofad-test-project/messages:send")
        self.assertEqual(kw["json"]["message"]["token"],self.token)
        self.assertNotIn(self.customer.phone,str(kw["json"]))
        self.assertEqual(kw["json"]["message"]["notification"]["title"],"KOFAD Market order update")
        self.assertEqual(kw["json"]["message"]["android"]["priority"],"NORMAL")

    def test_no_delivery_when_any_gate_off(self):
        with override_settings(KOFAD_NATIVE_PUSH_DELIVERY_ENABLED=False):
            self.assertFalse(delivery_ready())
            self.assertEqual(queue_order_push(self.order,"paid"),0)
            self.assertEqual(process_due_push(),0)
        self.assertFalse(MobilePushOutbox.objects.exists())
