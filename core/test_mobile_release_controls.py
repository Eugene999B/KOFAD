"""Android critical version policies and generic channel notices."""
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from core.mobile_release_models import MobileReleasePolicy, MobileNotice


class MobileControlTests(TestCase):
    @override_settings(KOFAD_CUSTOMER_APP_VERSION="1.4.0")
    def test_no_forced_update_without_verified_android_link(self):
        MobileReleasePolicy.objects.create(
            channel="customer", minimum_android_version="1.2.0",
            critical_update_reason="Security compatibility fix",
        )
        data = self.client.get("/market/app/native-version.json").json()
        self.assertEqual(data["android_policy"]["minimum_version"], "")

    @override_settings(
        KOFAD_CUSTOMER_APP_VERSION="1.4.0",
        KOFAD_CUSTOMER_APP_ANDROID_URL="https://play.google.com/store/apps/details?id=com.kofadimpex.market",
    )
    def test_minimum_android_version_enforced_only_up_to_published_release(self):
        policy = MobileReleasePolicy.objects.create(
            channel="customer", minimum_android_version="1.2.0",
            critical_update_reason="Critical account-protection update",
        )
        data = self.client.get("/market/app/native-version.json").json()
        self.assertEqual(data["android_policy"]["minimum_version"], "1.2.0")
        self.assertIn("account-protection", data["android_policy"]["reason"])
        policy.minimum_android_version = "2.0.0"
        policy.save()
        data = self.client.get("/market/app/native-version.json").json()
        self.assertEqual(data["android_policy"]["minimum_version"], "")

    def test_announcements_separated_and_unpublished_hidden(self):
        MobileNotice.objects.create(channel="customer", title="Service hours", message="Saturday opening times", enabled=True)
        MobileNotice.objects.create(channel="customer", title="Draft", message="Not yet approved", enabled=False)
        MobileNotice.objects.create(channel="staff", title="Team alert", message="Check your secure workspace", enabled=True)
        public = self.client.get("/market/app/native-version.json")
        self.assertContains(public, "Service hours")
        self.assertNotContains(public, "Team alert")
        self.assertNotContains(public, "Not yet approved")
        staff = self.client.get("/staff/app/native-version.json")
        self.assertContains(staff, "Team alert")
        self.assertNotContains(staff, "Service hours")

    def test_control_dashboard_superuser_only(self):
        self.assertEqual(self.client.get("/staff/app/control/").status_code, 302)
        ordinary = User.objects.create_user("staffer", password="strong-test-secret")
        self.client.force_login(ordinary)
        self.assertIn(self.client.get("/staff/app/control/").status_code, (302, 403))
        admin = User.objects.create_superuser("appowner", "admin@example.com", "test-strong-secret")
        self.client.force_login(admin)
        response = self.client.get("/staff/app/control/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mobile App Control")
        self.assertIn("no-store", response["Cache-Control"])
