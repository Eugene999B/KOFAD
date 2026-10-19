"""IPinfo Lite safety and owner-workflow regression tests; never call the internet."""
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.cache.backends.locmem import LocMemCache
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from core import integration_health, integration_live, ipinfo_lite
from marketplace.tests import MarketFixtures


TOKEN = "secret-ipinfo-token-must-never-render"
RECORD = {
    "ip": "8.8.8.8", "asn": "AS15169", "as_name": "Google LLC",
    "as_domain": "google.com", "country_code": "US",
    "country": "United States", "continent_code": "NA",
    "continent": "North America", "ignored_email": "private@example.test",
}


def reply(data=None, status=200):
    response = Mock(status_code=status)
    response.json.return_value = RECORD if data is None else data
    return response


@override_settings(KOFAD_IPINFO_LITE_ENABLED=True, KOFAD_IPINFO_LITE_TOKEN=TOKEN)
class IPinfoAdapterTests(SimpleTestCase):
    def setUp(self):
        self.cache = LocMemCache("ipinfo-adapter-tests", {})
        self.cache.clear()
        self.cache_patch = patch.object(ipinfo_lite, "cache", self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def test_public_only_no_urls_hostnames_or_private_addresses(self):
        invalid = ["", "8.8.8.8/24", "https://8.8.8.8", "localhost",
                   "10.0.0.1", "127.0.0.1", "169.254.169.254",
                   "192.168.1.1", "172.16.0.1", "0.0.0.0",
                   "::1", "fd00::1", "fe80::1",
                   "8.8.8.8:80", "not-an-ip", "2" * 1000]
        with patch.object(ipinfo_lite.requests, "get") as get:
            for value in invalid:
                with self.subTest(value=value):
                    with self.assertRaises(ipinfo_lite.InvalidPublicIP):
                        ipinfo_lite.lookup_public_ip(value)
            get.assert_not_called()
        self.assertEqual(str(ipinfo_lite.public_address("2001:4860:4860::8888")),
                         "2001:4860:4860::8888")

    def test_fixed_https_bearer_without_redirects_and_minimized_fields(self):
        with patch.object(ipinfo_lite.requests, "get", return_value=reply()) as get:
            result = ipinfo_lite.lookup_public_ip("8.8.8.8")
        self.assertEqual(result["country_code"], "US")
        self.assertEqual(result["asn"], "AS15169")
        self.assertEqual(set(result), set(ipinfo_lite.FIELDS))
        self.assertNotIn(TOKEN, str(result))
        self.assertNotIn("ignored_email", str(result))
        self.assertNotIn("8.8.8.8", str(result))
        self.assertEqual(get.call_args.args[0], "https://api.ipinfo.io/lite/8.8.8.8")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(get.call_args.kwargs["timeout"], (2, 4))
        self.assertFalse(get.call_args.kwargs["allow_redirects"])
        self.assertEqual(len(list(self.cache._cache)), 1)
        self.assertNotIn("8.8.8.8", str(list(self.cache._cache)))

    def test_country_cache_prevents_repeat_requests_and_can_be_bypassed(self):
        with patch.object(ipinfo_lite.requests, "get", return_value=reply()) as get:
            ipinfo_lite.lookup_public_ip("8.8.8.8")
            ipinfo_lite.lookup_public_ip("8.8.8.8")
            self.assertEqual(get.call_count, 1)
            ipinfo_lite.lookup_public_ip("8.8.8.8", use_cache=False)
            self.assertEqual(get.call_count, 2)

    def test_provider_error_wrong_ip_or_bad_country_fail_closed(self):
        failures = [
            reply(status=302), reply(status=401),
            reply({"ip": "1.1.1.1", "country_code": "US"}),
            reply({"ip": "8.8.8.8", "country_code": "not-a-country"}),
        ]
        with patch.object(ipinfo_lite.requests, "get", side_effect=failures):
            for _ in failures:
                with self.assertRaises(ipinfo_lite.IPinfoUnavailable) as raised:
                    ipinfo_lite.lookup_public_ip("8.8.8.8", use_cache=False)
                self.assertNotIn(TOKEN, str(raised.exception))

    @override_settings(KOFAD_IPINFO_LITE_ENABLED=False)
    def test_disabled_provider_stays_off_even_with_token(self):
        with patch.object(ipinfo_lite.requests, "get") as get:
            with self.assertRaises(ipinfo_lite.IPinfoUnavailable):
                ipinfo_lite.lookup_public_ip("8.8.8.8")
            get.assert_not_called()

    @override_settings(KOFAD_IPINFO_LITE_TOKEN="")
    def test_missing_token_prevents_network_request(self):
        with patch.object(ipinfo_lite.requests, "get") as get:
            with self.assertRaises(ipinfo_lite.IPinfoUnavailable):
                ipinfo_lite.lookup_public_ip("8.8.8.8")
            get.assert_not_called()

    def test_verification_uses_only_uncached_static_dns_ip(self):
        with patch.object(ipinfo_lite, "lookup_public_ip", return_value={
            "country_code": "US", "asn": "AS15169",
        }) as lookup:
            evidence = integration_live.verify("ipinfo_lite")
        lookup.assert_called_once_with("8.8.8.8", use_cache=False)
        self.assertEqual(evidence["state"], "verified")
        self.assertNotIn(TOKEN, str(evidence))
        self.assertNotIn("8.8.8.8", str(evidence))
        self.assertIn("VPN", str(evidence))


class IPinfoOwnerTests(MarketFixtures):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.staff.access.refresh_from_db()
        session = self.client.session
        session["access_version"] = self.staff.access.session_version
        session.save()

    @override_settings(KOFAD_IPINFO_LITE_ENABLED=True, KOFAD_IPINFO_LITE_TOKEN=TOKEN)
    def test_owner_detail_and_lookup_render_only_coarse_data(self):
        detail = self.client.get(reverse("connections_detail", args=["ipinfo_lite"]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Inspect a public IP address")
        self.assertNotContains(detail, TOKEN)
        with patch.object(ipinfo_lite, "lookup_public_ip", return_value={
            "country_code": "US", "country": "United States",
            "continent": "North America", "as_name": "Google LLC",
            "asn": "AS15169", "as_domain": "google.com",
        }) as lookup:
            response = self.client.post(reverse("ipinfo_lookup"), {"ip_address": "8.8.8.8"})
        self.assertEqual(response.status_code, 200)
        lookup.assert_called_once_with("8.8.8.8")
        self.assertContains(response, "Google LLC")
        self.assertNotContains(response, "8.8.8.8", html=False)  # placeholder is not the requested address
        self.assertNotContains(response, TOKEN)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("8.8.8.8", str(self.client.session.items()))

    @override_settings(KOFAD_IPINFO_LITE_ENABLED=True, KOFAD_IPINFO_LITE_TOKEN=TOKEN)
    def test_private_addresses_never_leave_system(self):
        with patch.object(ipinfo_lite, "lookup_public_ip") as lookup:
            response = self.client.post(reverse("ipinfo_lookup"), {"ip_address": "127.0.0.1"})
        self.assertContains(response, "Only publicly routable")
        lookup.assert_not_called()

    @override_settings(KOFAD_IPINFO_LITE_ENABLED=True, KOFAD_IPINFO_LITE_TOKEN=TOKEN)
    def test_owner_lookup_rate_budget(self):
        with patch.object(ipinfo_lite, "lookup_public_ip", return_value={"country_code": "US"}) as lookup:
            for _ in range(12):
                self.assertEqual(self.client.post(reverse("ipinfo_lookup"), {"ip_address": "8.8.8.8"}).status_code, 200)
            last = self.client.post(reverse("ipinfo_lookup"), {"ip_address": "8.8.8.8"})
        self.assertContains(last, "12 per 15 minutes")
        self.assertEqual(lookup.call_count, 12)
        self.assertNotIn("8.8.8.8", str(self.client.session.items()))

    def test_csrf_get_and_staff_authorization_boundaries(self):
        url = reverse("ipinfo_lookup")
        self.assertEqual(self.client.get(url).status_code, 405)
        secure = Client(enforce_csrf_checks=True)
        secure.cookies.update(self.client.cookies)
        self.assertEqual(secure.post(url, {"ip_address": "8.8.8.8"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url, {"ip_address": "8.8.8.8"}).status_code, 302)

        staff = User.objects.create_user("ipinfo-basic-user", password="password-for-testing")
        staff.access.branches.add(self.branch)
        self.client.force_login(staff)
        session = self.client.session
        session["access_version"] = staff.access.session_version
        session.save()
        with patch.object(ipinfo_lite, "lookup_public_ip") as lookup:
            self.assertEqual(self.client.post(url, {"ip_address": "8.8.8.8"}).status_code, 403)
            lookup.assert_not_called()

    @override_settings(KOFAD_IPINFO_LITE_ENABLED=True, KOFAD_IPINFO_LITE_TOKEN=TOKEN)
    def test_catalogue_reports_config_only_without_leaking_credentials(self):
        with patch.object(integration_health, "_evidence", return_value={}):
            item = next(x for x in integration_health.catalog()["items"] if x["id"] == "ipinfo_lite")
        self.assertEqual(item["requirements_ok"], 2)
        self.assertEqual(item["status"], "ready")
        self.assertNotIn(TOKEN, str(item))
