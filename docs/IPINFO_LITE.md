# IPinfo Lite — opt-in investigation tool

IPinfo Lite's free API provides country/continent and basic autonomous-system (ASN) information for a public IP address. It does **not** supply precise location, user identity, VPN/proxy/Tor detection, fraud scoring, or permission to block accounts. See the [official Lite schema](https://ipinfo.io/developers/lite-api).

## Connect

1. Create/sign into an authorised [IPinfo account](https://ipinfo.io/) and get the account's Lite API token.
2. Set `KOFAD_IPINFO_LITE_TOKEN` to that token in the **Railway web service's secret variables**. Do not commit or expose the token.
3. Set `KOFAD_IPINFO_LITE_ENABLED=1` on that service; deploy.
4. As the KOFAD system owner, visit **Staff → Settings → Connections → IPinfo Lite network context** and select **Verify provider now**. This sends an explicit read-only lookup of public DNS address `8.8.8.8` to IPinfo, without customer data. The result expires from the verification session after 15 minutes.
5. The owner-only **Inspect a public IP address** form is now available for authorised incident investigations.

When disabled or token absent, the network lookup fails closed. There is no login middleware and no automatic geolocation, profiling, or account blocking.

## Data minimisation

- Only manually entered global IPv4/IPv6 addresses are sent over HTTPS to the fixed `api.ipinfo.io` Lite endpoint with a server-side bearer token. Private, loopback, link-local and malformed inputs are rejected without network activity.
- Requests have short timeouts and disable HTTP redirects to protect the bearer credential.
- Only the seven Lite country/continent/ASN fields are kept for presentation. Other provider data, including the queried IP, is discarded.
- KOFAD doesn't write the queried IP or response body into its application database or application logs. Sanitised country/network context may be cached for one hour, under a secret-keyed digest of the IP, not plaintext.
- The response is sent with no-store cache headers and HTML escaping. The owner session stores lookup timestamps for limiting frequency (12 per 15 minutes), not the queried IP.
- Other infrastructure (load balancer, security proxy, server, IPinfo) may have its own access logs. Treat IPs as personal data and use the form only with a legitimate investigation purpose. Do not enter customer addresses casually.

## Limits and operations

- The Lite plan requires an IPinfo account token and is currently advertised by IPinfo as unlimited for its **Lite** fields only. Paid privacy/fraud capabilities are not included.
- A successful manual provider check proves API availability and token acceptance at the test instant, not fraud detection or all future lookups.
- The free-tier provider data is contextual and can be incomplete or inaccurate. Never deny purchases, password resets, sign-ins, staff sessions or access on this basis alone.
- For an outage, leave logins unaffected. Check **Settings → Connections** and Railway service outbound networking. There are no customer-facing dependencies.
- Tests: `python manage.py test core.test_ipinfo_lite --verbosity 2`. All provider calls are mocked.
