"""Upstash REST integration tests: all HTTP calls are mocked."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from core import integration_health, integration_live, upstash_redis
from marketplace import views as market_views


TOKEN = "secret-upstash-token-must-never-be-rendered"
URL = "https://clean-forest-12345.upstash.io"


def reply(value, *, status=200):
    response = Mock(status_code=status)
    response.json.return_value = {"result": value}
    return response


@override_settings(
    KOFAD_UPSTASH_REST_ENABLED=True,
    KOFAD_UPSTASH_REST_URL=URL,
    KOFAD_UPSTASH_REST_TOKEN=TOKEN,
)
class UpstashRESTTests(SimpleTestCase):
    def test_ping_requires_authenticated_https_and_returns_no_private_data(self):
        with patch.object(upstash_redis.requests, "post", return_value=reply("PONG")) as post:
            self.assertTrue(upstash_redis.ping())
        self.assertEqual(post.call_args.args, (URL,))
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["json"], ["PING"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(kwargs["timeout"], (2, 4))
        self.assertFalse(kwargs["allow_redirects"])

    def test_single_script_enforces_both_budgets_atomically(self):
        session_hash = "a" * 64
        with patch.object(upstash_redis.requests, "post", return_value=reply(1)) as post:
            self.assertTrue(upstash_redis.consume_map_budget(session_hash, 12345, 500, 40))
        parts = post.call_args.kwargs["json"]
        self.assertEqual(parts[0], "EVAL")
        self.assertIn("redis.call('INCR'", parts[1])
        self.assertEqual(parts[2:5], ["2", "kofad:map:global:12345", "kofad:map:session:" + session_hash + ":12345"])
        self.assertEqual(parts[5:], ["500", "40", "75"])
        self.assertNotIn(TOKEN, str(parts))
        self.assertTrue(all("127.0.0.1" not in str(value) for value in parts))

    def test_atomic_budget_can_deny_without_separate_roundtrip(self):
        with patch.object(upstash_redis.requests, "post", return_value=reply(0)) as post:
            self.assertFalse(upstash_redis.consume_map_budget("f" * 64, 23456, 100, 10))
        post.assert_called_once()

    def test_malformed_or_unsafe_hosts_rejected_before_request(self):
        bad = [
            "http://abc.upstash.io", "https://abc.upstash.io.evil.example",
            "https://abc.upstash.io@evil.example", "https://user@abc.upstash.io",
            "https://abc.upstash.io:443", "https://127.0.0.1",
            "https://abc.upstash.io/path", "https://abc.upstash.io/?token=secret",
            "https://abc.upstash.io/#fragment", "",
        ]
        with patch.object(upstash_redis.requests, "post") as post:
            for url in bad:
                with self.subTest(url=url), override_settings(KOFAD_UPSTASH_REST_URL=url):
                    self.assertFalse(upstash_redis.configured())
                    with self.assertRaises(upstash_redis.UpstashUnavailable):
                        upstash_redis.ping()
            post.assert_not_called()

    def test_missing_token_rejects_network_and_disabled_counter_remains_off(self):
        with patch.object(upstash_redis.requests, "post") as post:
            with override_settings(KOFAD_UPSTASH_REST_TOKEN=""):
                self.assertFalse(upstash_redis.configured())
                with self.assertRaises(upstash_redis.UpstashUnavailable):
                    upstash_redis.ping()
            with override_settings(KOFAD_UPSTASH_REST_ENABLED=False):
                # An owner may verify credentials first, but cannot spend the
                # shared map-search budget while the rollout flag is off.
                self.assertTrue(upstash_redis.configured())
                with self.assertRaises(upstash_redis.UpstashUnavailable):
                    upstash_redis.consume_map_budget("a" * 64, 23456, 500, 40)
            post.assert_not_called()

    def test_owner_can_verify_before_counter_rollout(self):
        with override_settings(KOFAD_UPSTASH_REST_ENABLED=False):
            with patch.object(upstash_redis.requests, "post", return_value=reply("PONG")):
                self.assertTrue(upstash_redis.ping())

    def test_provider_errors_do_not_expose_token_or_response_body(self):
        results = [reply(None, status=401), reply(None, status=302), reply("unexpected")]
        with patch.object(upstash_redis.requests, "post", side_effect=results):
            for _ in results:
                with self.assertRaises(upstash_redis.UpstashUnavailable) as raised:
                    upstash_redis.ping()
                self.assertNotIn(TOKEN, str(raised.exception))
        with patch.object(upstash_redis.requests, "post", return_value=reply({"unsafe": "private"})):
            with self.assertRaises(upstash_redis.UpstashUnavailable):
                upstash_redis.consume_map_budget("b" * 64, 23456, 100, 10)

    def test_invalid_script_arguments_rejected_without_http(self):
        with patch.object(upstash_redis.requests, "post") as post:
            for args in [
                ("session-raw", 23456, 500, 40),
                ("a" * 64, -1, 500, 40),
                ("a" * 64, 23456, 0, 40),
                ("a" * 64, 23456, 500, False),
            ]:
                with self.subTest(args=args):
                    with self.assertRaises(upstash_redis.UpstashUnavailable):
                        upstash_redis.consume_map_budget(*args)
            post.assert_not_called()

    def test_read_only_staff_verifier_and_catalogue(self):
        with patch.object(upstash_redis, "ping", return_value=True) as ping:
            result = integration_live.verify("upstash_redis")
        ping.assert_called_once_with()
        self.assertEqual(result["state"], "verified")
        self.assertNotIn(TOKEN, str(result))
        item = next(row for row in integration_health.CATALOG if row["id"] == "upstash_redis")
        config = integration_health._configuration()
        self.assertTrue(all(config[key] for _, key in item["requirements"]))
        self.assertNotIn(TOKEN, str(item))
        self.assertNotIn(TOKEN, str(config))

    def test_verified_connection_status_removes_stale_warning(self):
        # Mock the module's connection reference, not a method on Django's
        # shared connection proxy (which corrupts class-level test teardown).
        with patch.object(integration_health, "_evidence", return_value={}), \
             patch.object(integration_health, "connection") as db:
            db.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
            before = integration_health.catalog()
            after = integration_health.catalog(probes={"upstash_redis": {"state": "verified"}})

        pending = next(item for item in before["items"] if item["id"] == "upstash_redis")
        verified = next(item for item in after["items"] if item["id"] == "upstash_redis")
        self.assertIn("credentials have NOT been tested", " ".join(pending["issues"]))
        self.assertEqual(verified["status"], "working")
        self.assertNotIn("credentials have NOT been tested", " ".join(verified["issues"]))
        self.assertIn("Monitor Free-tier usage", verified["repair"])
        self.assertNotIn(TOKEN, str(verified))

    def test_market_budget_uses_shared_provider_only_when_enabled(self):
        class SessionStub:
            session_key = "non-public-session-identifier"
            def save(self):
                raise AssertionError("Existing session should not be re-saved")
        request = SimpleNamespace(session=SessionStub())
        with patch.object(upstash_redis, "consume_map_budget", return_value=True) as budget, \
             patch.object(market_views.cache, "add") as local_add:
            self.assertTrue(market_views._consume_map_lookup_budget(request))
        self.assertEqual(budget.call_count, 1)
        self.assertEqual(len(budget.call_args.args[0]), 64)
        self.assertNotIn("non-public-session", str(budget.call_args.args))
        local_add.assert_not_called()

    def test_metered_map_queries_fail_closed_on_upstash_outage(self):
        request = SimpleNamespace(session=SimpleNamespace(session_key="opaque-session"))
        with patch.object(upstash_redis, "consume_map_budget", side_effect=upstash_redis.UpstashUnavailable("down")), \
             patch.object(market_views.cache, "add") as local_add:
            self.assertFalse(market_views._consume_map_lookup_budget(request))
            local_add.assert_not_called()
