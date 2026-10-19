"""Geoapify Ghana geocoding is optional, bounded and failure-tolerant."""
from unittest.mock import Mock, patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from . import services


@override_settings(
    KOFAD_GEOAPIFY_ENABLED=True,
    KOFAD_GEOAPIFY_API_KEY="test-only-geoapify-key",
    GOOGLE_MAPS_SERVER_KEY="",
)
class GeoapifyFallbackTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("marketplace.services.requests.get")
    def test_ghana_text_search_normalizes_results_and_never_exposes_key(self, get):
        get.return_value.json.return_value = {"results": [
            {"formatted": "Accra, Ghana", "lat": 5.55, "lon": -0.19, "country_code": "gh"},
            {"formatted": "Lagos, Nigeria", "lat": 6.45, "lon": 3.39, "country_code": "ng"},
        ]}
        rows = services.location_search("Accra")
        self.assertEqual(rows, [{"label": "Accra, Ghana", "latitude": 5.55, "longitude": -0.19}])
        args, kwargs = get.call_args
        self.assertEqual(args, (services.GEOAPIFY_SEARCH,))
        self.assertEqual(kwargs["params"]["filter"], "countrycode:gh")
        self.assertEqual(kwargs["params"]["limit"], 5)
        self.assertEqual(kwargs["params"]["apiKey"], "test-only-geoapify-key")
        self.assertNotIn("test-only-geoapify-key", str(rows))

    @patch("marketplace.services.requests.get")
    def test_reverse_uses_geoapify_without_changing_quote_calculations(self, get):
        get.return_value.json.return_value = {
            "results": [{"formatted": "Kumasi, Ghana", "lat": 6.69, "lon": -1.62}]
        }
        self.assertEqual(
            services.reverse_location("6.69", "-1.62"),
            {"label": "Kumasi, Ghana"},
        )
        args, kwargs = get.call_args
        self.assertEqual(args, (services.GEOAPIFY_REVERSE,))
        self.assertEqual(kwargs["params"]["lat"], "6.69")
        self.assertEqual(kwargs["params"]["lon"], "-1.62")

    @patch("marketplace.services._nominatim_headers", return_value={"User-Agent": "KOFAD test"})
    @patch("marketplace.services.requests.get")
    def test_provider_error_uses_existing_nominatim_fallback(self, get, _headers):
        fallback = Mock()
        fallback.json.return_value = [{"display_name": "Tamale, Ghana", "lat": "9.4", "lon": "-0.83"}]
        get.side_effect = [requests.Timeout("private URL with API key"), fallback]
        rows = services.location_search("Tamale")
        self.assertEqual(rows[0]["label"], "Tamale, Ghana")
        self.assertEqual(get.call_count, 2)
        self.assertEqual(get.call_args_list[0].args[0], services.GEOAPIFY_SEARCH)
        self.assertEqual(get.call_args_list[1].args[0], services.NOMINATIM_SEARCH)

    @override_settings(KOFAD_GEOAPIFY_ENABLED=False)
    @patch("marketplace.services._nominatim_headers", return_value={"User-Agent": "KOFAD test"})
    @patch("marketplace.services.requests.get")
    def test_disabled_provider_preserves_existing_search(self, get, _headers):
        get.return_value.json.return_value = []
        self.assertEqual(services.location_search("Takoradi"), [])
        self.assertEqual(get.call_args.args[0], services.NOMINATIM_SEARCH)

    @patch("marketplace.services._nominatim_headers", return_value={"User-Agent": "KOFAD test"})
    @patch("marketplace.services.requests.get")
    @patch("marketplace.services.cache.incr", return_value=services.GEOAPIFY_DAILY_CAP + 1)
    @patch("marketplace.services.cache.add", side_effect=[False, True])
    def test_daily_limit_never_queries_geoapify(self, _add, _incr, get, _headers):
        get.return_value.json.return_value = []
        self.assertEqual(services.location_search("Cape Coast"), [])
        self.assertEqual(get.call_args.args[0], services.NOMINATIM_SEARCH)

    @patch("marketplace.services.requests.get")
    def test_untrusted_bad_coordinates_cannot_escape_into_response(self, get):
        get.return_value.json.return_value = {"results": [
            {"formatted": "Invalid", "lat": "NaN", "lon": 1, "country_code": "gh"},
            {"formatted": "Invalid", "lat": None, "lon": 1, "country_code": "gh"},
            {"formatted": "Valid", "lat": 7, "lon": -1, "country_code": "gh"},
        ]}
        result = services.location_search("Ghana")
        self.assertEqual(result, [{"label": "Valid", "latitude": 7.0, "longitude": -1.0}])
