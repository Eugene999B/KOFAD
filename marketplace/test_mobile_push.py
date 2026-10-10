"""KOFAD push registration tests: real native authorization and explicit consent only."""
import hashlib
import json
from datetime import timedelta

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.tests import Fixtures
from marketplace import services
from marketplace.mobile_auth_models import MobileDeviceSession
from marketplace.mobile_push import _cipher
from marketplace.mobile_push_models import MobilePushSubscription
from marketplace.models import CustomerAccount

@override_settings(
    ALLOWED_HOSTS=["testserver","market.kofadimpex.com","staff.kofadimpex.com"],
    PRIVILEGED_MFA_ENFORCED=False,
    KOFAD_NATIVE_AUTH_ENABLED=True,
    KOFAD_NATIVE_PUSH_ENABLED=True,
)
class NativePushConsentTests(Fixtures, TestCase):
    customer_url="/market/mobile/v1/push/devices/"
    staff_url="/staff/mobile/v1/push/devices/"
    fcm="fcm_token_for_test_device_2026:abcdef123456"

    def setUp(self):
        self.setup_data()
        self.customer=CustomerAccount.objects.create(
            phone="0241115555",full_name="Push customer",active=True,password_hash="hashed-test-value",
        )
        self.customer_token="C"*43
        self.staff_token="S"*43
        self.customer_session=MobileDeviceSession.objects.create(
            channel="customer",customer=self.customer,
            customer_credential_stamp=services.customer_credential_stamp(self.customer),
            access_hash=hashlib.sha256(self.customer_token.encode()).hexdigest(),
            refresh_hash=hashlib.sha256(("R"*43).encode()).hexdigest(),
            access_expires_at=timezone.now()+timedelta(minutes=15),
            refresh_expires_at=timezone.now()+timedelta(days=1),
        )
        self.staff_session=MobileDeviceSession.objects.create(
            channel="staff",staff_user=self.user,branch=self.branch,
            staff_access_version=self.user.access.session_version,
            access_hash=hashlib.sha256(self.staff_token.encode()).hexdigest(),
            refresh_hash=hashlib.sha256(("T"*43).encode()).hexdigest(),
            access_expires_at=timezone.now()+timedelta(minutes=15),
            refresh_expires_at=timezone.now()+timedelta(hours=5),
        )

    def request(self,method="get",channel="customer",token=None,body=None,origin="https://localhost"):
        client=Client()
        path=self.customer_url if channel=="customer" else self.staff_url
        host="market.kofadimpex.com" if channel=="customer" else "staff.kofadimpex.com"
        headers={"HTTP_HOST":host,"HTTP_ORIGIN":origin}
        if token: headers["HTTP_AUTHORIZATION"]="Bearer "+token
        if method=="post":
            return client.post(path,data=json.dumps(body),content_type="application/json",
                               secure=True,**headers)
        if method=="delete":return client.delete(path,secure=True,**headers)
        if method=="options":return client.options(path,secure=True,**headers)
        return client.get(path,secure=True,**headers)

    def payload(self,*,token=None,service=True,marketing=False):
        return {"token":token or self.fcm,"service_opt_in":service,"marketing_opt_in":marketing}

    def test_customer_registration_is_encrypted_and_never_returned(self):
        result=self.request("post",token=self.customer_token,body=self.payload(marketing=True))
        self.assertEqual(result.status_code,200,result.content[:200])
        self.assertTrue(result.json()["registered"])
        self.assertFalse(result.json()["background_delivery_active"])
        self.assertNotIn(self.fcm,result.content.decode())
        subscription=MobilePushSubscription.objects.get(device_session=self.customer_session)
        self.assertNotIn(self.fcm,subscription.encrypted_token)
        self.assertEqual(_cipher().decrypt(subscription.encrypted_token.encode()).decode(),self.fcm)
        self.assertTrue(subscription.marketing_opt_in)
        overview=self.request(token=self.customer_token).json()
        self.assertTrue(overview["service_opt_in"])
        self.assertTrue(overview["marketing_opt_in"])
        self.assertNotIn(self.fcm,str(overview))
        self.assertEqual(self.request("post",token=self.customer_token,body=self.payload(marketing=True)).status_code,200)
        self.assertEqual(MobilePushSubscription.objects.count(),1)

    def test_staff_never_registers_marketing(self):
        denied=self.request("post","staff",self.staff_token,self.payload(marketing=True))
        self.assertEqual(denied.status_code,400)
        self.assertFalse(MobilePushSubscription.objects.exists())
        allowed=self.request("post","staff",self.staff_token,self.payload())
        self.assertEqual(allowed.status_code,200)
        self.assertFalse(self.request(channel="staff",token=self.staff_token).json()["marketing_opt_in"])
        self.assertEqual(self.request(channel="customer",token=self.staff_token).status_code,401)

    def test_device_unsubscribe_revoke_session_and_consent_off(self):
        self.assertEqual(self.request("post",token=self.customer_token,body=self.payload()).status_code,200)
        self.assertEqual(self.request("post",token=self.customer_token,
            body=self.payload(service=False,marketing=False)).status_code,200)
        self.assertFalse(MobilePushSubscription.objects.exists())
        self.request("post",token=self.customer_token,body=self.payload())
        self.assertEqual(self.request("delete",token=self.customer_token).status_code,200)
        self.assertFalse(MobilePushSubscription.objects.exists())
        self.request("post",token=self.customer_token,body=self.payload())
        self.customer_session.revoked_at=timezone.now()
        self.customer_session.save(update_fields=["revoked_at"])
        self.assertEqual(self.request(token=self.customer_token).status_code,401)

    def test_logout_deletes_subscription(self):
        self.request("post",token=self.customer_token,body=self.payload())
        resp=Client().post("/market/mobile/v1/revoke/",
            content_type="application/json",data="{}",
            HTTP_HOST="market.kofadimpex.com",HTTP_ORIGIN="https://localhost",
            HTTP_AUTHORIZATION="Bearer "+self.customer_token,secure=True)
        self.assertEqual(resp.status_code,200)
        self.assertFalse(MobilePushSubscription.objects.exists())

    def test_feature_gates_origins_method_limits_and_cookie_only_fail(self):
        self.assertEqual(self.request().status_code,401)
        self.assertEqual(self.request("post",body=self.payload()).status_code,401)
        blocked=self.request(token=self.customer_token,origin="https://unknown.example")
        self.assertEqual(blocked.status_code,403)
        self.assertNotIn("Access-Control-Allow-Origin",blocked)
        self.assertEqual(self.request("options").status_code,204)
        with override_settings(KOFAD_NATIVE_PUSH_ENABLED=False):
            self.assertEqual(self.request(token=self.customer_token).status_code,404)
        with override_settings(KOFAD_NATIVE_AUTH_ENABLED=False):
            self.assertEqual(self.request(token=self.customer_token).status_code,404)

    def test_strict_payload_validation_does_not_store_prohibited_data(self):
        cases=[
            {"token":self.fcm,"service_opt_in":1,"marketing_opt_in":False},
            {"token":"short","service_opt_in":True,"marketing_opt_in":False},
            {"token":self.fcm,"service_opt_in":True,"marketing_opt_in":False,"customer_id":13},
            {"token":self.fcm,"service_opt_in":True},
            {"token":self.fcm,"service_opt_in":True,"marketing_opt_in":"yes"},
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.request("post",token=self.customer_token,body=body).status_code,400)
        self.assertFalse(MobilePushSubscription.objects.exists())
