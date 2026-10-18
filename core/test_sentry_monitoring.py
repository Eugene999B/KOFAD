"""Sentry privacy boundary and opt-in initialization regression tests."""
import json
import logging
from unittest.mock import patch

from django.test import SimpleTestCase

from core.sentry_monitoring import (
    configure_sentry,
    discard_breadcrumb,
    safe_error_sample_rate,
    sanitized_error_event,
)


class PrivateSentryTests(SimpleTestCase):
    def test_scrubber_allowlists_only_exception_code_locations(self):
        confidential = "customer-phone-0551234567-bank-balance-secret"
        event = {
            "event_id": "abc123",
            "level": "error",
            "environment": "production",
            "request": {"url": "/reset/SECRET", "data": {"password": confidential}},
            "user": {"email": confidential, "ip_address": confidential},
            "contexts": {"trace": {"op": confidential}},
            "breadcrumbs": {"values": [{"message": confidential}]},
            "extra": {"payment": confidential},
            "tags": {"card_reference": confidential},
            "logentry": {"formatted": confidential},
            "message": confidential,
            "exception": {"values": [{
                "type": "ValueError",
                "value": confidential,
                "stacktrace": {"frames": [{
                    "filename": "marketplace/views.py",
                    "function": "place_order",
                    "lineno": 123,
                    "module": "marketplace.views",
                    "in_app": True,
                    "vars": {"password": confidential},
                    "context_line": confidential,
                    "pre_context": [confidential],
                }]},
            }]},
        }
        result = sanitized_error_event(event, {})
        encoded = json.dumps(result)
        self.assertNotIn(confidential, encoded)
        self.assertNotIn("/reset/SECRET", encoded)
        self.assertEqual(
            set(result),
            {"event_id", "level", "environment", "exception"},
        )
        self.assertEqual(result["exception"]["values"][0]["value"], "[redacted]")
        self.assertEqual(
            set(result["exception"]["values"][0]["stacktrace"]["frames"][0]),
            {"filename", "function", "lineno", "module", "in_app"},
        )
        self.assertEqual(result["exception"]["values"][0]["type"], "ValueError")

    def test_messages_without_exceptions_are_dropped(self):
        self.assertIsNone(sanitized_error_event({"message": "secret"}, {}))
        self.assertIsNone(sanitized_error_event({"logentry": {"formatted": "secret"}}, {}))
        self.assertIsNone(discard_breadcrumb({"message": "secret"}, {}))

    def test_invalid_dsn_is_not_used_or_logged(self):
        with patch("core.sentry_monitoring.sentry_sdk.init") as init:
            for dsn in ("", "http://key@sentry.example/1", "https://no-username.example/1"):
                with self.subTest(dsn=bool(dsn)):
                    self.assertFalse(configure_sentry(dsn))
            init.assert_not_called()

    def test_sample_rate_never_accepts_nan_or_out_of_bounds(self):
        for value in ("invalid", float("nan"), -1, 2, None):
            self.assertEqual(safe_error_sample_rate(value), 0.25)
        self.assertEqual(safe_error_sample_rate("0.5"), 0.5)

    @patch("core.sentry_monitoring.sentry_sdk.init")
    def test_valid_dsn_inits_without_pii_traces_or_replays(self, init):
        dsn = "https://public-key@o123.ingest.sentry.io/456"
        self.assertTrue(configure_sentry(
            dsn, environment="production", release="test-commit-sha", sample_rate="0.5",
        ))
        kwargs = init.call_args.kwargs
        self.assertEqual(kwargs["dsn"], dsn)
        self.assertEqual(kwargs["release"], "test-commit-sha")
        self.assertFalse(kwargs["send_default_pii"])
        self.assertFalse(kwargs["include_local_variables"])
        self.assertFalse(kwargs["default_integrations"])
        self.assertFalse(kwargs["auto_enabling_integrations"])
        self.assertEqual(kwargs["max_request_body_size"], "never")
        self.assertEqual(kwargs["traces_sample_rate"], 0)
        self.assertEqual(kwargs["profiles_sample_rate"], 0)
        self.assertEqual(kwargs["sample_rate"], 0.5)
        self.assertEqual(kwargs["max_breadcrumbs"], 0)
        self.assertIsNone(kwargs["before_send_transaction"]({"trace": "secret"}, {}))
        self.assertIsNone(kwargs["before_breadcrumb"]({"message": "secret"}, {}))
        self.assertEqual(len(kwargs["integrations"]), 2)
