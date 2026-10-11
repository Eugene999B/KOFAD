"""Customer-initiated Ghana delivery address lookup; no map pin or DB schema change.

Google Places returns a place ID before a separate explicit user selection fetches
coordinates. GhanaPostGPS validation uses a separately configured address provider;
a valid-looking digital address is never labelled verified without provider proof.
"""
import logging
import re
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.exceptions import ValidationError

from . import services

logger = logging.getLogger(__name__)

PLACES_AUTOCOMPLETE = "https://places.googleapis.com/v1/places:autocomplete"
PLACES_DETAILS = "https://places.googleapis.com/v1/places/"
DIGITAL_ADDRESS = re.compile(r"^[A-Z]{2,3}-[0-9]{3,4}-[0-9]{4}$")
PLACE_ID = re.compile(r"^[a-zA-Z0-9_-]{5,180}$")
SESSION_TOKEN = re.compile(r"^[a-zA-Z0-9_-]{1,36}$")


def normalize_digital_address(value):
    value = str(value or "").upper().strip().replace(" ", "")
    if not DIGITAL_ADDRESS.fullmatch(value):
        raise ValidationError("Enter a GhanaPost GPS code like AK-039-5028.")
    return value


def _ghana_coordinates(latitude, longitude):
    try:
        lat = Decimal(str(latitude))
        lng = Decimal(str(longitude))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError("Location service returned invalid coordinates.") from exc
    if not (lat.is_finite() and lng.is_finite()
            and Decimal("4.5") <= lat <= Decimal("11.3")
            and Decimal("-3.5") <= lng <= Decimal("1.3")):
        raise ValidationError("Location service returned a place outside Ghana.")
    return float(lat), float(lng)


def _google_key():
    return str(getattr(settings, "GOOGLE_MAPS_SERVER_KEY", "") or "").strip()


def _token(token):
    token = str(token or "").strip()
    return token if SESSION_TOKEN.fullmatch(token) else ""


def _fallback_search(query):
    # Existing Ghana-restricted geocoding/Nominatim search remains functional
    # when paid Google Places is not configured or temporarily unavailable.
    return [{**row, "source": "address_search"} for row in services.location_search(query)]


def suggest_landmarks(query, session_token=""):
    query = str(query or "").strip()[:160]
    if len(query) < 3:
        raise ValidationError("Enter at least 3 characters of a landmark or address.")
    key = _google_key()
    if not key:
        return _fallback_search(query)
    payload = {
        "input": query,
        "includedRegionCodes": ["gh"],
        "regionCode": "gh",
        "languageCode": "en",
    }
    token = _token(session_token)
    if token:
        payload["sessionToken"] = token
    try:
        response = requests.post(
            PLACES_AUTOCOMPLETE,
            json=payload,
            headers={
                "X-Goog-Api-Key": key,
                "X-Goog-FieldMask": "suggestions.placePrediction.placeId,suggestions.placePrediction.text.text",
            },
            timeout=settings.GOOGLE_MAPS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        rows = []
        for suggestion in (body.get("suggestions") or [])[:8]:
            prediction = suggestion.get("placePrediction") or {}
            place_id = str(prediction.get("placeId") or "")
            if PLACE_ID.fullmatch(place_id):
                rows.append({
                    "label": str((prediction.get("text") or {}).get("text") or query)[:240],
                    "place_id": place_id,
                    "source": "google_places",
                })
        return rows
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        logger.warning("Google Places suggestions unavailable: %s", type(exc).__name__)
        return _fallback_search(query)


def select_landmark(place_id, session_token=""):
    place_id = str(place_id or "").strip()
    if not PLACE_ID.fullmatch(place_id):
        raise ValidationError("Select a valid location suggestion.")
    key = _google_key()
    if not key:
        raise ValidationError("Google landmark lookup is not configured.")
    token = _token(session_token)
    headers = {
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "displayName,formattedAddress,location,addressComponents",
    }
    if token:
        # Place Details shares the autocomplete session to avoid per-keystroke billing.
        headers["X-Goog-FieldMask"] += ",id"
    try:
        response = requests.get(
            PLACES_DETAILS + place_id,
            params={"sessionToken": token} if token else {},
            headers=headers,
            timeout=settings.GOOGLE_MAPS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or "location" not in body:
            raise ValidationError("That landmark has no usable location. Choose another.")
        country = [
            str(part.get("shortText") or "").upper()
            for part in body.get("addressComponents") or []
            if "country" in (part.get("types") or [])
        ]
        if country and "GH" not in country:
            raise ValidationError("Please select a location within Ghana.")
        point = body["location"]
        lat, lng = _ghana_coordinates(point.get("latitude"), point.get("longitude"))
        return {
            "label": str(body.get("formattedAddress") or
                         (body.get("displayName") or {}).get("text") or "Selected landmark")[:240],
            "latitude": lat, "longitude": lng,
            "place_id": place_id, "source": "google_places",
        }
    except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning("Google Places details unavailable: %s", type(exc).__name__)
        raise ValidationError("Could not confirm this landmark right now. Try another search.") from exc


def resolve_digital_address(value):
    code = normalize_digital_address(value)
    url = str(getattr(settings, "GHANAPOST_GPS_LOOKUP_URL", "") or "").strip()
    if not url:
        raise ValidationError(
            "Automated GhanaPost GPS verification is not connected yet. "
            "You can still search a nearby landmark or use your current location."
        )
    if not url.startswith("https://") or "@" in url or "?" in url or "#" in url:
        raise ValidationError("GhanaPost address lookup is not securely configured.")
    token = str(getattr(settings, "GHANAPOST_GPS_LOOKUP_TOKEN", "") or "").strip()
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        response = requests.get(
            url,
            params={"address": code},
            headers=headers,
            timeout=getattr(settings, "GHANAPOST_GPS_TIMEOUT_SECONDS", 8),
            allow_redirects=False,
        )
        response.raise_for_status()
        if response.is_redirect:
            raise ValidationError("The address provider redirected unexpectedly.")
        body = response.json()
        if not isinstance(body, dict) or body.get("success") is False or body.get("found") is False:
            raise ValidationError("This GhanaPost GPS code was not found. Check the code.")
        details = body.get("data") if isinstance(body.get("data"), dict) else body
        if isinstance(details.get("Table"), list):
            details = details["Table"][0] if details["Table"] else {}
        lat, lng = _ghana_coordinates(
            details.get("latitude", details.get("CenterLatitude")),
            details.get("longitude", details.get("CenterLongitude")),
        )
        returned_code = str(details.get("gps_name") or details.get("GPSName") or "").upper().replace("-", "")
        if returned_code and returned_code != code.replace("-", ""):
            raise ValidationError("The location provider returned a different digital address.")
        area = str(details.get("area") or details.get("Area") or details.get("district") or
                   details.get("District") or "Ghana")[:100]
        return {
            "code": code, "latitude": lat, "longitude": lng,
            "label": f"{code} · {area}", "source": "ghanapost_gps",
            "verified": True,
        }
    except (requests.RequestException, ValueError, TypeError, AttributeError, KeyError) as exc:
        logger.warning("Configured GhanaPost GPS address provider unavailable: %s", type(exc).__name__)
        raise ValidationError("GhanaPost GPS verification is temporarily unavailable. Search a landmark instead.") from exc
