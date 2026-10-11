"""Ghana delivery address finder verification. No live provider calls."""
from unittest.mock import Mock, patch

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from .delivery_locations import (
    normalize_digital_address, resolve_digital_address, select_landmark, suggest_landmarks,
)
from .forms import CheckoutForm
from .tests import MarketFixtures


class GhanaLocationTests(TestCase):
    def test_code_format(self):
        self.assertEqual(normalize_digital_address(" ak-039-5028 "), "AK-039-5028")
        with self.assertRaises(ValidationError):
            normalize_digital_address("bad-code")

    @override_settings(GOOGLE_MAPS_SERVER_KEY="")
    @patch("marketplace.delivery_locations.services.location_search")
    def test_search_without_google_key(self, search):
        search.return_value = [{"label": "KNUST", "latitude": "6.6", "longitude": "-1.5"}]
        self.assertEqual(suggest_landmarks("KNUST")[0]["source"], "address_search")
        search.assert_called_once_with("KNUST")

    @override_settings(GOOGLE_MAPS_SERVER_KEY="fake-key")
    @patch("marketplace.delivery_locations.requests.post")
    def test_google_place_prediction(self, post):
        response = Mock()
        response.json.return_value = {"suggestions": [{"placePrediction": {
            "placeId": "ChIJabcdefgh1234", "text": {"text": "Accra Mall, Ghana"}
        }}]}
        post.return_value = response
        row = suggest_landmarks("Accra Mall", "test-session")[0]
        self.assertEqual(row["place_id"], "ChIJabcdefgh1234")
        self.assertNotIn("latitude", row)
        self.assertEqual(post.call_args.kwargs["json"]["includedRegionCodes"], ["gh"])

    @override_settings(GOOGLE_MAPS_SERVER_KEY="fake-key")
    @patch("marketplace.delivery_locations.requests.get")
    def test_selected_landmark_only_inside_ghana(self, get):
        response = Mock()
        response.json.return_value = {
            "formattedAddress": "Accra Mall, Ghana",
            "location": {"latitude": 5.62, "longitude": -0.17},
            "addressComponents": [{"types": ["country"], "shortText": "GH"}],
        }
        get.return_value = response
        self.assertEqual(select_landmark("ChIJabcdefgh1234")["latitude"], 5.62)
        response.json.return_value["addressComponents"][0]["shortText"] = "US"
        with self.assertRaises(ValidationError):
            select_landmark("ChIJabcdefgh1234")
