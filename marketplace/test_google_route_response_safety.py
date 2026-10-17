"""Unexpected Google Routes JSON must never crash KOFAD delivery checkout.

Tests are isolated and do not call Google or place an order.
"""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from marketplace import services


@override_settings(GOOGLE_MAPS_SERVER_KEY="nonproduction-test-key")
class GoogleRouteResponseSafetyTests(SimpleTestCase):
    @patch("marketplace.services.requests.post")
    def test_unexpected_json_shapes_and_invalid_distances_use_existing_fallback(self, request):
        response = Mock(status_code=200)
        request.return_value = response
        invalid = [
            [], {"routes": {}}, {"routes": [None]}, {"routes": []},
            {"routes": [{"distanceMeters": None}]},
            {"routes": [{"distanceMeters": "-1"}]},
            {"routes": [{"distanceMeters": "NaN"}]},
            {"routes": [{"distanceMeters": "Infinity"}]},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                response.json.return_value = payload
                self.assertIsNone(
                    services._google_route("5.603700", "-0.186900", "5.604000", "-0.187000")
                )
        self.assertEqual(request.call_count, len(invalid))

    @patch("marketplace.services.requests.post")
    def test_valid_route_preserves_distance_and_duration(self, request):
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {
            "routes": [{"distanceMeters": 1275, "duration": "85s",
                        "polyline": {"encodedPolyline": "original-route"}}],
        }
        quote = services._google_route("5.603700", "-0.186900", "5.604000", "-0.187000")
        self.assertEqual(str(quote["distance_km"]), "1.275")
        self.assertEqual(quote["duration_seconds"], 85)
        self.assertEqual(quote["polyline"], "original-route")
        self.assertEqual(quote["source"], "google_route")

    @patch("marketplace.services.requests.post")
    def test_malformed_polyline_does_not_lose_valid_distance(self, request):
        request.return_value = Mock(status_code=200)
        request.return_value.json.return_value = {
            "routes": [{"distanceMeters": 1500, "duration": "bad",
                        "polyline": ["unexpected"]}],
        }
        quote = services._google_route("5.603700", "-0.186900", "5.604000", "-0.187000")
        self.assertEqual(str(quote["distance_km"]), "1.5")
        self.assertEqual(quote["duration_seconds"], 0)
        self.assertEqual(quote["polyline"], "")
