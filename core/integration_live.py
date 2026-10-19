"""Manual, explicitly initiated, low-cost provider verification.

Only fixed read-only HTTPS API calls. No user-supplied URL, no paid messages,
token refresh, payment capture, webhook mutation or backup creation. Responses
are reduced to small non-sensitive evidence objects before entering sessions.
"""
from datetime import datetime, timedelta, timezone as dt_timezone

import requests
from django.conf import settings
from django.utils import timezone

TIMEOUT = (2, 5)
VALID = frozenset({
    "brevo", "brevo_reporting", "brevo_webhook",
    "cloudflare", "inbound_email", "paystack", "google_login", "ipinfo_lite",
})
LIFETIME_SECONDS = 15 * 60


def _result(state, summary, checks, *, scope="Provider-side read-only metadata"):
    return {
        "state": state, "summary": summary,
        "checks": checks[:10], "scope": scope,
        "checked_at": timezone.now().isoformat(),
    }


def _check(label, state, detail):
    # Inputs to this helper MUST be authored or safely categorized values;
    # never feed it provider response dumps, token strings or customer data.
    return {"name": label, "state": state, "detail": detail[:260]}


def _get(url, *, headers=None, params=None):
    # Fixed URLs authored below: no redirects to unknown origins, no SSRF.
    try:
        response = requests.get(
            url, headers=headers or {}, params=params or {},
            timeout=TIMEOUT, allow_redirects=False,
        )
    except (requests.Timeout, requests.ConnectionError):
        return None, "timeout"
    except requests.RequestException:
        return None, "network"
    if 300 <= response.status_code < 400:
        return None, "redirect"
    if response.status_code in (401, 403):
        return None, "unauthorized"
    if response.status_code == 429:
        return None, "rate_limit"
    if response.status_code >= 500:
        return None, "provider_error"
    if response.status_code != 200:
        return None, "unexpected"
    try:
        payload = response.json()
        if not isinstance(payload, dict):
            return None, "schema"
    except (ValueError, UnicodeError):
        return None, "schema"
    return payload, None


ERROR_REASON = {
    "timeout": "Provider timed out; connectivity is not proven. Retry later and inspect outbound HTTPS access.",
    "network": "KOFAD could not establish a provider HTTPS connection. Check DNS, TLS and provider availability.",
    "redirect": "Provider unexpectedly redirected a read-only API call; do not follow redirects with API keys.",
    "unauthorized": "Provider rejected the credentials or their permissions (HTTP 401/403). Check the account-scoped API key.",
    "rate_limit": "Provider reported API rate limiting (HTTP 429). Let the limit reset before retrying.",
    "provider_error": "Provider returned a 5xx response. This does not prove KOFAD has a configuration problem.",
    "unexpected": "Provider rejected the request with a non-success response. Check provider API status and access.",
    "schema": "Provider returned an unexpected response shape. Review API changes before trusting this check.",
}


def _brevo_auth():
    key = getattr(settings, "KOFAD_BREVO_API_KEY", "")
    return {"api-key": key, "accept": "application/json"} if key else None


def _brevo():
    headers = _brevo_auth()
    if not headers:
        return _result("not_configured", "Brevo API key is missing.", [
            _check("Brevo API key", "failed", "Configure the KOFAD Brevo key on the web service."),
        ])
    account, error = _get("https://api.brevo.com/v3/account", headers=headers)
    if error:
        return _result("failed", "Brevo account verification was unsuccessful.", [
            _check("Authenticated Brevo account", "failed", ERROR_REASON[error]),
        ])
    senders, sender_error = _get(
        "https://api.brevo.com/v3/senders", headers=headers,
        params={"domain": "kofadimpex.com"},
    )
    plans = account.get("plan", [])
    cap = None
    if isinstance(plans, list):
        for plan in plans[:10]:
            if isinstance(plan, dict) and plan.get("creditsType") == "sendLimit":
                try:
                    cap = max(0, min(int(plan.get("credits", 0)), 100000))
                except (ValueError, TypeError):
                    pass
    checks = [_check("Brevo account API", "passed", "Brevo accepted this application's API key over HTTPS.")]
    if cap is not None:
        checks.append(_check("Provider send allowance", "passed", f"Provider reports a send limit of {cap} credits; this is not a guaranteed remaining balance."))
    if sender_error:
        checks.append(_check("Verified business senders", "failed", ERROR_REASON[sender_error]))
        return _result("partial", "Brevo account authenticated, but sender verification could not complete.", checks)
    entries = senders.get("senders", [])
    if not isinstance(entries, list):
        return _result("partial", "Brevo account authenticated, but the senders response was unexpected.", checks + [
            _check("Verified business senders", "failed", ERROR_REASON["schema"])
        ])
    approved = {str(item.get("email", "")).lower() for item in entries
                if isinstance(item, dict) and item.get("active") is True
                and str(item.get("email", "")).lower().endswith("@kofadimpex.com")}
    expected = {
        str(settings.KOFAD_BREVO_TRANSACTION_FROM_EMAIL).lower(),
        str(settings.KOFAD_BREVO_SECURITY_FROM_EMAIL).lower(),
    }
    missing = sorted(addr for addr in expected if addr and addr not in approved)
    checks.append(_check(
        "Brevo KOFAD sender registry", "failed" if missing else "passed",
        (f"{len(approved)} active KOFAD domain senders; {len(missing)} of two configured sending addresses not active in Brevo."
         if missing else f"{len(approved)} active KOFAD domain senders; KOFAD's configured sending identities appear active."),
    ))
    if missing:
        return _result("failed", "Brevo authenticated, but a configured KOFAD sender is not active.", checks)
    return _result("verified", "Brevo API key and configured KOFAD senders authenticated successfully.", checks +
                   [_check("Actual email delivery", "unknown", "A working API and active sender do not prove recipient delivery. Use Brevo Reporting or webhook receipts.")])


def _brevo_reporting():
    headers = _brevo_auth()
    if not headers:
        return _result("not_configured", "Cannot read Brevo reports without the KOFAD Brevo API key.", [])
    today = timezone.localdate()
    data, error = _get(
        "https://api.brevo.com/v3/smtp/statistics/events",
        headers=headers,
        params={"limit": 30, "startDate": (today - timedelta(days=7)).isoformat(),
                "endDate": today.isoformat()},
    )
    if error:
        return _result("failed", "Brevo's transactional event-reporting API could not be verified.", [
            _check("Brevo transactional reports", "failed", ERROR_REASON[error]),
        ])
    events = data.get("events", [])
    if not isinstance(events, list):
        return _result("partial", "Brevo reporting API returned an unexpected event format.", [
            _check("Brevo reports response", "failed", ERROR_REASON["schema"])
        ])
    counts = {}
    for row in events[:30]:
        if isinstance(row, dict):
            event = str(row.get("event", "")).lower()
            if event in {"delivered", "sent", "request", "deferred", "hard_bounce", "soft_bounce",
                         "blocked", "spam", "invalid_email", "error", "opened", "clicks"}:
                counts[event] = counts.get(event, 0) + 1
    delivery = counts.get("delivered", 0)
    failures = sum(counts.get(n, 0) for n in ("hard_bounce", "blocked", "spam", "invalid_email", "error"))
    checks = [
        _check("Brevo transactional reporting API", "passed", "Authenticated HTTPS GET returned transactional event-report data."),
        _check("Event sample", "passed", f"Latest sampled events in the past seven days: {len(events)}; delivered {delivery}, hard failures/complaints {failures}. This is a sample, not an exhaustive seven-day total."),
        _check("KOFAD reporting webhook", "passed" if len(getattr(settings, "KOFAD_BREVO_WEBHOOK_TOKEN", "")) >= 32 else "unknown",
               "KOFAD has a configured inbound verification token." if len(getattr(settings, "KOFAD_BREVO_WEBHOOK_TOKEN", "")) >= 32
               else "KOFAD has no configured webhook token. Provider reports can work independently without automatic KOFAD webhook updates."),
    ]
    return _result("verified", "Brevo's authenticated event-reporting API is accessible. This does not verify KOFAD's separate webhook.", checks)


def _webhook():
    from core.email_models import EmailDeliveryEvent
    configured = len(getattr(settings, "KOFAD_BREVO_WEBHOOK_TOKEN", "")) >= 32
    if not configured:
        return _result("failed", "KOFAD cannot authenticate inbound Brevo delivery webhooks.", [
            _check("KOFAD webhook bearer secret", "failed", "The server's KOFAD_BREVO_WEBHOOK_TOKEN is missing or too short."),
            _check("Provider event reports", "unknown", "Brevo may still have delivery events in its API independently of this webhook."),
        ], scope="KOFAD webhook configuration and received event evidence")
    latest = EmailDeliveryEvent.objects.order_by("-received_at").values("received_at", "event").first()
    if not latest:
        return _result("partial", "KOFAD webhook token is configured, but no authenticated delivery receipt has been recorded.", [
            _check("KOFAD webhook secret", "passed", "Secret configured; not displayed."),
            _check("Authenticated event receipt", "unknown", "No provider event received yet. Confirm the provider's webhook URL and bearer token match."),
        ], scope="KOFAD webhook configuration and received event evidence")
    age = timezone.now() - latest["received_at"]
    recent = age <= timedelta(days=7)
    return _result("verified" if recent else "partial", "KOFAD has recorded an authenticated Brevo webhook receipt." if recent else "Most recent Brevo receipt is older than seven days.", [
        _check("KOFAD webhook secret", "passed", "Secret configured; not displayed."),
        _check("Latest authenticated receipt", "passed" if recent else "unknown",
               "Most recent recorded event was "+latest["received_at"].strftime("%Y-%m-%d %H:%M UTC")+
               " ("+str(latest["event"]).replace("_", " ")+")."),
    ], scope="KOFAD webhook configuration and received event evidence")


def _cloudflare(which):
    zone_id = getattr(settings, "KOFAD_CLOUDFLARE_ZONE_ID", "")
    token = getattr(settings, "KOFAD_CLOUDFLARE_READ_TOKEN", "")
    checks = []
    # Public resolver data only shows active DNS records, NOT Cloudflare
    # account, worker route, or email forwarding delivery.
    data, error = _get(
        "https://dns.google/resolve",
        params={"name": "kofadimpex.com", "type": "MX"},
    )
    if error:
        checks.append(_check("Public MX lookup", "failed", ERROR_REASON[error]))
        return _result("failed", "Public domain mail DNS verification was unsuccessful.", checks, scope="Public MX and optional authenticated Cloudflare account state")
    answers = data.get("Answer", []) if data.get("Status") == 0 else []
    mx = [str(x.get("data", "")).lower() for x in answers if isinstance(x, dict) and x.get("type") == 15]
    has_cf = len([x for x in mx if "mx.cloudflare.net" in x]) >= 2
    checks.append(_check("Public MX records", "passed" if has_cf else "failed",
                         "Public DNS resolves multiple Cloudflare Email Routing MX hosts." if has_cf
                         else "Cloudflare Email Routing MX records could not be confirmed. Check zone DNS and propagation."))
    if not has_cf:
        return _result("failed", "Public mail DNS does not show the expected Cloudflare MX records.", checks)
    if not (len(token) >= 25 and len(zone_id) == 32 and all(c in "0123456789abcdef" for c in zone_id.lower())):
        return _result("partial", "Cloudflare DNS verified; private routing rules and Worker state are not authenticated in this website.", checks + [
            _check("Cloudflare read-only API access", "unknown",
                   "Optional KOFAD_CLOUDFLARE_READ_TOKEN and KOFAD_CLOUDFLARE_ZONE_ID are not both configured on the web service."),
        ])
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    base = "https://api.cloudflare.com/client/v4/zones/" + zone_id + "/email/routing"
    routing, re = _get(base, headers=headers)
    if re:
        return _result("partial", "Public MX works, but authenticated Cloudflare routing inspection failed.", checks + [
            _check("Cloudflare Email Routing API", "failed", ERROR_REASON[re])
        ])
    routing_result = routing.get("result", {})
    enabled = bool(routing.get("success")) and isinstance(routing_result, dict) and (
        routing_result.get("enabled") is True and routing_result.get("status") == "ready")
    checks.append(_check("Cloudflare inbound routing service", "passed" if enabled else "failed",
                         "Cloudflare reports Email Routing enabled and ready." if enabled
                         else "Cloudflare Email Routing is not enabled/ready. Inspect zone settings."))
    rules, rules_error = _get(base + "/rules", headers=headers)
    if rules_error:
        return _result("partial", "Cloudflare settings checked; mail routing rule list was not accessible.", checks + [
            _check("Email destination rules", "failed", ERROR_REASON[rules_error])
        ])
    entries = rules.get("result", [])
    if not isinstance(entries, list):
        return _result("partial", "Cloudflare routing rules format was unexpected.", checks)
    active = [r for r in entries if isinstance(r, dict) and r.get("enabled") is True]
    worker = sum(1 for r in active if any(
        isinstance(a, dict) and a.get("type") == "worker"
        for a in r.get("actions", []) if isinstance(r.get("actions", []), list)
    ))
    forwarded = sum(1 for r in active if any(
        isinstance(a, dict) and a.get("type") == "forward"
        for a in r.get("actions", []) if isinstance(r.get("actions", []), list)
    ))
    checks.append(_check("Email routing rules", "passed" if active else "failed",
                         f"{len(active)} enabled rules; {worker} Worker-routed and {forwarded} forwarding rules. Destination addresses are not exposed."))
    if which == "inbound_email" and worker == 0:
        return _result("failed", "No enabled Cloudflare rule delivers mail to KOFAD's inbound Worker.", checks)
    if which == "inbound_email" and not getattr(settings, "KOFAD_EMAIL_CENTER_ENABLED", False):
        return _result("failed", "Cloudflare Worker routing is active, but KOFAD's email centre is disabled.", checks)
    if not enabled or not active:
        return _result("failed", "Cloudflare domain email routing is not fully ready.", checks)
    return _result("verified", "Cloudflare authenticated: Email Routing enabled, MX points to Cloudflare and rules are active.", checks +
                   [_check("End-to-end mailbox delivery", "unknown",
                           "Routing rules are healthy, but inbox arrival needs a real received-email event.")],
                   scope="Authenticated Cloudflare routing configuration; no test email sent")


def _paystack():
    key = getattr(settings, "PAYSTACK_SECRET_KEY", "")
    if not key:
        return _result("not_configured", "KOFAD does not have a Paystack secret key.", [])
    data, error = _get("https://api.paystack.co/balance", headers={"Authorization": "Bearer " + key})
    if error:
        return _result("failed", "Paystack authenticated read-only verification failed.", [
            _check("Paystack API credential", "failed", ERROR_REASON[error]),
        ])
    if data.get("status") is not True:
        return _result("failed", "Paystack did not confirm a successful account response.", [
            _check("Paystack API response", "failed", "Authenticated API returned a non-success status."),
        ])
    return _result("verified", "Paystack accepted the API key on a read-only account request.", [
        _check("Authenticated Paystack API", "passed", "Balance endpoint returned success. Financial amounts were discarded."),
        _check("Payment processing", "unknown", "This checks API access only; no checkout, MoMo debit, refund or callback was attempted."),
    ])


def _google():
    if not getattr(settings, "KOFAD_GOOGLE_OAUTH_ENABLED", False):
        return _result("not_configured", "KOFAD Google sign-in is disabled in settings.", [])
    data, error = _get("https://accounts.google.com/.well-known/openid-configuration")
    if error:
        return _result("failed", "Google's public OpenID discovery endpoint could not be reached.", [
            _check("Google OpenID discovery", "failed", ERROR_REASON[error]),
        ])
    valid = data.get("issuer") == "https://accounts.google.com" and str(
        data.get("authorization_endpoint", "")
    ).startswith("https://accounts.google.com/o/oauth2/")
    return _result("partial" if valid else "failed",
                   "Google identity service responds; KOFAD's OAuth client secret was NOT validated."
                   if valid else "Google returned an unexpected identity configuration.", [
                       _check("Google public OAuth discovery", "passed" if valid else "failed",
                              "Provider discovery is accessible; only a real completed sign-in verifies the KOFAD client and callback."),
                   ], scope="Public Google OpenID discovery only")


def _ipinfo_lite():
    """Authenticate the Lite token using a known public address, not a customer IP."""
    from .ipinfo_lite import IPinfoUnavailable, is_configured, lookup_public_ip

    scope = "Explicit uncached IPinfo Lite lookup of public DNS test address only"
    if not is_configured():
        return _result("not_configured", "IPinfo Lite is disabled or its token is missing.", [
            _check("IPinfo Lite configuration", "failed", "Add the server-side token and enable the feature to verify it."),
        ], scope=scope)
    try:
        result = lookup_public_ip("8.8.8.8", use_cache=False)
    except IPinfoUnavailable:
        return _result("failed", "IPinfo Lite could not authenticate or return valid country data.", [
            _check("Authenticated IPinfo Lite lookup", "failed",
                   "Check the token, provider availability and egress access. No private customer address was sent."),
        ], scope=scope)
    return _result("verified", "IPinfo Lite returned country/ASN data for the test address.", [
        _check("Authenticated IPinfo Lite lookup", "passed", "Read-only HTTPS lookup succeeded; no customer IP was sent."),
        _check("Fraud/VPN identification", "unknown", "IPinfo Lite does not provide paid proxy, VPN or fraud-detection fields."),
    ], scope=scope)


def verify(service_id):
    if service_id not in VALID:
        raise ValueError("This service does not support safe live verification.")
    return {
        "brevo": _brevo, "brevo_reporting": _brevo_reporting,
        "brevo_webhook": _webhook, "cloudflare": lambda: _cloudflare("cloudflare"),
        "inbound_email": lambda: _cloudflare("inbound_email"),
        "paystack": _paystack, "google_login": _google,
        "ipinfo_lite": _ipinfo_lite,
    }[service_id]()


def recent_probes(session):
    """Session-backed 15-minute results, portable across Railway web replicas."""
    now = timezone.now()
    values = session.get("kofad_integration_live_checks", {})
    if not isinstance(values, dict):
        return {}
    usable = {}
    for key, value in values.items():
        if key not in VALID or not isinstance(value, dict):
            continue
        try:
            stamp = datetime.fromisoformat(str(value.get("checked_at", "")))
            if stamp.tzinfo is None or not timedelta(0) <= now - stamp <= timedelta(seconds=LIFETIME_SECONDS):
                continue
        except (ValueError, TypeError):
            continue
        if value.get("state") not in {"verified", "partial", "failed", "not_configured"}:
            continue
        usable[key] = value
    return usable
