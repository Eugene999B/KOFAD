from unittest.mock import ANY, patch
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware
from django.contrib.auth.models import AnonymousUser
from . import services
from .tests import MarketFixtures


class CustomerAccountChangeTests(MarketFixtures):
    def sign_in(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()
        self.client.get("/market/account/")

    def test_phone_change_requires_password_then_otp_and_preserves_history(self):
        self.customer.set_password("test-customer-password")
        self.customer.save()
        self.sign_in()
        with patch("marketplace.services.send_otp") as send:
            response = self.client.post("/market/account/security/", {
                "action": "phone_start", "new_phone": "0245550021", "current_password": "wrong"})
            send.assert_not_called()
            self.assertEqual(response.status_code, 302)
            self.client.post("/market/account/security/", {
                "action": "phone_start", "new_phone": "0245550021", "current_password": "test-customer-password"})
            send.assert_called_once_with("+233245550021", "change_phone", request=ANY)
        with patch("marketplace.services.verify_otp") as verify:
            self.client.post("/market/account/security/", {
                "action": "phone_verify", "code": "123456", "current_password": "test-customer-password"})
            verify.assert_called_once_with("+233245550021", "123456", "change_phone")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.phone, "+233245550021")
        self.assertEqual(self.client.session["market_customer_id"], self.customer.pk)

    def test_password_change_invalidates_an_existing_other_session(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        SessionMiddleware(lambda request: None).process_request(request)
        services.set_customer_session(request, self.customer)
        self.assertIsNotNone(services.customer_from_session(request))
        self.customer.set_password("new-unrelated-password")
        self.customer.save(update_fields=["password_hash"])
        self.assertIsNone(services.customer_from_session(request))

    def test_anonymous_customer_cannot_change_phone(self):
        response = self.client.post("/market/account/security/", {
            "action": "phone_verify", "code": "123456", "current_password": "test-password"})
        self.assertEqual(response.status_code, 302)
        self.customer.refresh_from_db()
        self.assertNotEqual(self.customer.phone, "+233245550021")
