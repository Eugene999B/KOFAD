"""Staff mobile bootstrap refuses anonymous users and enforces branch permissions."""
from django.test import TestCase, override_settings
from core.tests import Fixtures


@override_settings(
    ALLOWED_HOSTS=["localhost", "testserver", "staff.kofadimpex.com", "market.kofadimpex.com"],
    PRIVILEGED_MFA_ENFORCED=False,
)
class StaffMobileV1Tests(Fixtures, TestCase):
    def setUp(self):
        self.setup_data()

    def test_unauthenticated_staff_api_never_exposes_business_data(self):
        response = self.client.get("/staff/mobile/v1/bootstrap/", HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "authentication_required"})
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.client.post("/staff/mobile/v1/bootstrap/").status_code, 405)

    def test_verified_existing_staff_session_returns_only_owned_scope(self):
        self.authenticate_client()
        response = self.client.get("/staff/mobile/v1/bootstrap/", HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertEqual(response.status_code, 200, response.get("Location", ""))
        info = response.json()
        self.assertEqual(info["channel"], "staff")
        self.assertEqual(info["branch"]["id"], self.branch.pk)
        self.assertTrue(info["permissions"]["sales"])
        self.assertFalse(info["features"]["native_financial_mutations"])
        self.assertFalse(info["features"]["native_mobile_token_login"])
        self.assertNotIn("Access-Control-Allow-Credentials", response)

    def test_staff_scope_is_rechecked_after_branch_selection_change(self):
        self.authenticate_client()
        session = self.client.session
        session["branch"] = 999999
        session.save()
        response = self.client.get("/staff/mobile/v1/bootstrap/", HTTP_HOST="staff.kofadimpex.com", secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "select_authorized_branch")
