from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from .models import Conversation
from .public_content import PAGES


class PublicSiteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.data = {"name": "Enquiry visitor", "phone": "0245550090",
                     "topic": "Customer feedback", "message": "Please clarify a product pack size.",
                     "consent": "on", "website": ""}

    def test_every_information_page_is_public_and_linked(self):
        for slug, page in PAGES.items():
            slug = "returns-policy" if slug == "returns" else slug
            response = self.client.get("/" + slug + "/")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, page["title"])
            self.assertContains(response, "/privacy/")
        self.assertContains(self.client.get("/"), "/about/")
        self.assertContains(self.client.get("/market/access/"), "/returns-policy/")

    @patch("marketplace.services.send_transactional_sms")
    def test_feedback_reaches_staff_inbox_without_sending_sms(self, send):
        response = self.client.post("/contact/", self.data)
        self.assertRedirects(response, "/contact/")
        item = Conversation.objects.get()
        self.assertEqual(item.public_phone, "+233245550090")
        self.assertEqual(item.subject, "Website · Customer feedback")
        self.assertIsNone(item.customer)
        self.assertEqual(item.messages.get().sender_type, "visitor")
        self.assertEqual(item.messages.get().body, self.data["message"])
        send.assert_not_called()

    def test_feedback_validation_and_consent_are_required(self):
        for change in ({"phone": "invalid"}, {"consent": ""}, {"message": "short"}, {"website": "spam"}):
            response = self.client.post("/contact/", {**self.data, **change})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(Conversation.objects.exists())

    def test_feedback_is_throttled_and_csrf_protected(self):
        self.client.post("/contact/", self.data)
        response = self.client.post("/contact/", self.data)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(Conversation.objects.count(), 1)
        csrf_client = Client(enforce_csrf_checks=True)
        self.assertEqual(csrf_client.post("/contact/", self.data).status_code, 403)

    def test_public_feedback_is_not_exposed_to_other_visitors(self):
        self.client.post("/contact/", {**self.data, "message": "<script>alert('private')</script>"})
        response = Client().get("/contact/")
        self.assertNotContains(response, "alert('private')")
        self.assertNotContains(response, "Enquiry visitor")

    @override_settings(ALLOWED_HOSTS=["kofadimpex.com", "market.kofadimpex.com", "staff.kofadimpex.com"])
    def test_policy_routes_stay_public_and_wrong_host_posts_do_not_forward(self):
        for slug in PAGES:
            slug = "returns-policy" if slug == "returns" else slug
            response = self.client.get("/" + slug + "/", HTTP_HOST="kofadimpex.com")
            self.assertEqual(response.status_code, 200)
            response = self.client.get("/" + slug + "/", HTTP_HOST="market.kofadimpex.com")
            self.assertEqual(response["Location"], "https://kofadimpex.com/" + slug + "/")
        response = self.client.post("/contact/", self.data, HTTP_HOST="market.kofadimpex.com")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Conversation.objects.exists())

    @override_settings(ALLOWED_HOSTS=["staff.kofadimpex.com"])
    def test_staff_returns_route_is_not_replaced_by_public_policy(self):
        from django.urls import resolve
        self.assertEqual(resolve("/returns/").url_name, "returns")
        response = self.client.get("/returns/", HTTP_HOST="staff.kofadimpex.com")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
