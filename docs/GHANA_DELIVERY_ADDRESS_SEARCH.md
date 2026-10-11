# Ghana customer delivery address search

Checkout now uses an address finder instead of a manual customer map pin. Staff dispatch and customer delivery tracking maps are unchanged.

## Customer journey

1. Search for a school, hospital, junction, public business, road, area, or other Ghana landmark. Choose a suggestion to fill confirmed destination coordinates.
2. Alternatively, enter a GhanaPost GPS code such as `AK-039-5028` and choose **Find this code**. Coordinates are displayed only after a configured provider verifies that code. The submitted code is stored with the order either way.
3. Alternatively, choose **Use my current location** and grant browser geolocation access.
4. Enter the town and useful house/street instructions for the delivery rider. Delivery distance, quote, and payment flow remain the existing verified server-side mechanisms. Saved customer addresses still prefill checkout.

All search and resolution endpoints require an authenticated customer or staff session and share the existing lookup quota; GhanaPost code lookup and Google Place Details are CSRF-protected POST requests. The exact customer GPS lookup is made only after button selection.

## Provider connection (not yet enabled in production)

- `GOOGLE_MAPS_SERVER_KEY`: a server-side credential with **Places API (New)**, Place Details (New), and existing Geocoding/Routes APIs enabled. Restrict it to the backend server where supported; control quota and set cost alerts in Google Cloud. Suggestions are limited to Ghana and grouped by a browser session token. Never expose this secret in frontend HTML.
- Optional `GHANAPOST_GPS_LOOKUP_URL`: an HTTPS endpoint you have **contracted or approved**, returning a JSON object containing matching digital address (e.g. GPSName) and latitude/longitude, ideally region/district. Example provider-compatible schema: `{ "success": true, "data": { "GPSName": "AK0395028", "CenterLatitude": 6.68, "CenterLongitude": -1.57 } }`. An example independent third-party provider is Afrihex; it is NOT GhanaPost's official platform. Check licensing, uptime, privacy, rate limits and costs before configuration.
- Optional `GHANAPOST_GPS_LOOKUP_TOKEN`: bearer token for that provider; leave empty only when provider permits tokenless access.
- `GHANAPOST_GPS_TIMEOUT_SECONDS`: optional timeout (default eight seconds).

Never use Google OAuth login credentials as Maps credentials. Never put credentials in GitHub or frontend JavaScript. No provider is charged or contacted in automated tests. If the provider is unavailable, the user can still choose a nearby landmark or current location; no address coordinates are fabricated.

A digital address identifies an address grid location, not proof that a person owns a property and not their live position. Customers should still provide nearby instructions. Real-time delivery tracking is a separate existing workflow.

## Rollout checklist

- [ ] Backend and frontend tests pass on newest `railway-release`
- [ ] Confirm checkout landmark search, GhanaPost lookup, current-location, saved addresses and pickup in a browser
- [ ] Configure the authorized Google Places key and optional GhanaPost lookup provider under Railway service variables
- [ ] Confirm customer-approved location details, Google attribution, provider billing safeguards
- [ ] Verify distance-based delivery fees and order addresses with synthetic records; no real charge
