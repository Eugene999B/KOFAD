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

    @override_settings(GHANAPOST_GPS_LOOKUP_URL="")
    @patch("marketplace.delivery_locations.requests.get")
    def test_unconfigured_gps_provider_does_not_invent_coordinates(self, get):
        with self.assertRaisesMessage(ValidationError, "not connected"):
            resolve_digital_address("AK-039-5028")
        get.assert_not_called()

    @override_settings(GHANAPOST_GPS_LOOKUP_URL="https://api.afrihex.com/v1/lookup")
    @patch("marketplace.delivery_locations.requests.get")
    def test_verifies_postal_code_against_provider_coordinates(self, get):
        response = Mock(is_redirect=False)
        response.json.return_value = {"success": True, "data": {
            "GPSName": "AK0395028", "CenterLatitude": 6.68,
            "CenterLongitude": -1.57, "District": "Kumasi",
        }}
        get.return_value = response
        location = resolve_digital_address("ak-039-5028")
        self.assertTrue(location["verified"])
        self.assertEqual(location["area"], "Kumasi")
        self.assertEqual(get.call_args.kwargs["params"], {"address": "AK-039-5028"})
        response.json.return_value["data"]["GPSName"] = "AK0000000"
        with self.assertRaisesMessage(ValidationError, "different digital address"):
            resolve_digital_address("AK-039-5028")

    def test_customer_checkout_normalizes_postal_code_without_map_picker(self):
        form = CheckoutForm(data={
            "fulfilment": "delivery", "recipient_name": "Ama Mensah",
            "phone": "0241234567", "email": "ama@example.test",
            "town": "Kumasi", "address_line": "Near hospital",
            "ghana_post_gps": "ak-039-5028",
            "latitude": "6.680000", "longitude": "-1.570000",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["ghana_post_gps"], "AK-039-5028")

class GhanaLocationEndpointTests(MarketFixtures):
    def sign_in(self):
        session = self.client.session
        session["market_customer_id"] = self.customer.pk
        session.save()

    def test_guests_do_not_access_customer_address_search(self):
        self.assertEqual(self.client.get("/market/location/suggest/?q=Accra").status_code, 401)
        self.assertEqual(self.client.post("/market/location/ghana-post/", {"code": "AK-039-5028"}).status_code, 401)

    @patch("marketplace.delivery_locations.suggest_landmarks")
    def test_customer_can_find_landmarks(self, suggest):
        self.sign_in()
        suggest.return_value = [{"label": "KNUST", "latitude": 6.6, "longitude": -1.5}]
        response = self.client.get("/market/location/suggest/?q=KNUST")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["label"], "KNUST")
