"""Read-only, low-cost integration diagnostics for the private staff Settings portal.

Rules:
* No external HTTP calls, SMS sends, payment charges, token refreshes or OAuth
  handshakes on dashboard render. Avoid Railway usage spikes.
* Only existing local configuration and accepted DB evidence are observed.
* A key present != a working service. Submitted != delivered.
* Secrets, recipient addresses, API payloads and tokens NEVER leave this module.
* Hosted/third-party configuration that Django cannot read stays "unverified".
"""
import logging
import os
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Count
from django.utils import timezone

logger = logging.getLogger(__name__)

GROUPS = (
    ("core", "Hosting, source and storage"),
    ("google", "Google accounts and workspace"),
    ("communications", "Email, messaging and support"),
    ("payments", "Payment providers"),
    ("growth", "Analytics and business tools"),
)
STATUS = {
    "working": ("Recent success", "good"),
    "ready": ("Configured · unverified", "pending"),
    "warning": ("Needs verification", "pending"),
    "issue": ("Needs attention", "bad"),
    "not_connected": ("Not connected", "neutral"),
    "external": ("Check provider console", "neutral"),
    "planned": ("Not integrated", "neutral"),
}

# Metadata has no sensitive values. URLs here are KOFAD internal or public
# provider landing pages, never an account link bearing an access token.
CATALOG = (
    dict(id="railway", group="core", name="Railway hosting", mark="RW",
         purpose="Runs KOFAD's Django web application and background worker.",
         steps=("GitHub release code builds into a Railway container.",
                "Railway runs the web and message-worker services.",
                "Health probes guard the deployment; billing and restarts stay in Railway."),
         requirements=(("Railway runtime detected", "railway_runtime"),),
         repair="Check Railway deployment health, web/worker logs, resource limits and usage. Runtime presence is not a service-status API.",
         manage="/settings/", external_note="This portal cannot read Railway billing, replicas or deployment history without an authorised Railway API connection."),
    dict(id="postgres", group="core", name="PostgreSQL database", mark="DB",
         purpose="Stores KOFAD business, customer, stock, finance, and job data.",
         steps=("Django receives an authenticated database connection.",
                "A read-only SELECT 1 confirms current query availability.",
                "Backups and recovery are managed separately."),
         requirements=(("PostgreSQL configured", "postgres_configured"),),
         repair="Check Railway Postgres health, DATABASE_URL binding, connection limits, and migrations. Never reset the live database to clear an alert.",
         manage="/settings/backup/"),
    dict(id="github", group="core", name="GitHub source and releases", mark="GH",
         purpose="Stores KOFAD source code and release workflows.",
         steps=("Developers commit to the KOFAD repository.",
                "GitHub Actions validates proposed code.",
                "The release branch triggers the existing Railway deployments."),
         requirements=(), repair="Inspect GitHub Actions, protected branches and the exact deployed commit. Repository checks cannot be verified using Django alone.",
         manage="https://github.com/Eugene999B/KOFAD", external=True),
    dict(id="cloudflare", group="core", name="Cloudflare DNS and edge", mark="CF",
         purpose="Manages KOFAD domains, DNS records and optional CDN/security features.",
         steps=("DNS resolves KOFAD domain names.",
                "Proxy/WAF apply only to traffic configured to traverse Cloudflare.",
                "TLS, DNS health and firewall analytics belong to the Cloudflare account."),
         requirements=(), repair="Verify each DNS record, proxy mode, TLS certificate and edge challenge in Cloudflare. A public page loading does not prove the WAF is enabled.",
         manage="https://dash.cloudflare.com/", external=True),
    dict(id="pwned_passwords", group="core", name="Pwned Passwords security", mark="PW",
         purpose="Screens newly chosen customer and staff passwords against known breach data using the free HIBP API.",
         steps=("Customer and staff password-setting forms use the shared Django validator.",
                "KOFAD sends only a five-character SHA-1 prefix to HIBP over HTTPS.",
                "Positive-count local suffix matches are rejected; upstream outages retain local password-policy validation."),
         requirements=(("Breach-password screening enabled", "pwned_passwords_enabled"),),
         repair="Set KOFAD_PWNED_PASSWORDS_ENABLED=1 on the Railway web service after release validation, then verify customer/staff password forms. This local flag does not prove HIBP availability.",
         manage="https://haveibeenpwned.com/API/v3"),
    dict(id="sentry", group="core", name="Sentry error monitoring", mark="SE",
         purpose="Receives minimized backend exception metadata for marketplace, staff and optional messaging worker diagnostics.",
         steps=("An error is captured by the Django or worker exception integration.",
                "KOFAD removes request data, identities, exception values and stack locals before transport.",
                "The approved Sentry project groups code-location errors and provides alerting."),
         requirements=(("Sentry reporting enabled", "sentry_enabled"),
                       ("HTTPS Sentry project DSN installed", "sentry_dsn")),
         repair="Create an approved Sentry Python/Django project and set KOFAD_SENTRY_DSN plus KOFAD_SENTRY_ENABLED=1 on the Railway service. Configuration presence does not verify delivery.",
         manage="https://sentry.io"),
    dict(id="ipinfo_lite", group="core", name="IPinfo Lite network context", mark="IP",
         purpose="Owner-initiated public-IP lookups for country and network operator (ASN) during security investigations; never automatically blocks logins.",
         steps=("An authorised system owner supplies a public IPv4 or IPv6 address in Connections.",
                "KOFAD sends that address to IPinfo Lite over HTTPS only after explicit submission; token stays server-side.",
                "Only coarse country, continent and ASN fields are shown. No identity, VPN, proxy or fraud verdict is provided."),
         requirements=(("IPinfo Lite investigation enabled", "ipinfo_enabled"),
                       ("IPinfo Lite token installed", "ipinfo_token")),
         repair="Create a free IPinfo account and store its Lite token as KOFAD_IPINFO_LITE_TOKEN in the Railway web service, then set KOFAD_IPINFO_LITE_ENABLED=1. Use Verify provider now. Do not enable automatic geo-blocking.",
         manage="https://ipinfo.io/dashboard"),
    dict(id="upstash_redis", group="core", name="Upstash Redis REST", mark="RD",
         purpose="Optional cross-worker atomic rate limits for paid customer map searches; other services retain their current limits.",
         steps=("A dedicated Upstash database exposes an authenticated HTTPS REST endpoint.",
                "KOFAD submits one atomic Lua script to enforce the global and session budget in the same request.",
                "Provider failures deny only metered map searches; checkout, staff login and local cache do not depend on Upstash."),
         requirements=(("Upstash REST rate limiting enabled", "upstash_enabled"),
                       ("Upstash REST endpoint configured", "upstash_url"),
                       ("Upstash REST token installed", "upstash_token")),
         repair="Create a dedicated Upstash Redis database and put UPSTASH's REST URL and token in KOFAD_UPSTASH_REST_URL and KOFAD_UPSTASH_REST_TOKEN on Railway. Verify PING before enabling KOFAD_UPSTASH_REST_ENABLED=1. Never store the token in source control.",
         manage="https://console.upstash.com/"),
    dict(id="oracle", group="core", name="Oracle Cloud", mark="OC",
         purpose="Optional future hosting or external database infrastructure.",
         steps=("Oracle resources must be provisioned and authorised.",
                "An application connection and tested migration plan must be created.",
                "No Oracle connection is defined in this Django runtime."),
         requirements=(), repair="Only activate after migration/backup planning and costs approval; do not interpret an Oracle account registration as a live KOFAD database link.",
         external=True, planned=True, manage="https://cloud.oracle.com/"),
    dict(id="google_login", group="google", name="Google customer and staff sign-in", mark="G",
         purpose="OpenID Connect sign-in for customers and linked staff accounts.",
         steps=("A customer or staff member taps Continue with Google.",
                "KOFAD validates Google identity with PKCE, state and nonce.",
                "An existing linked account signs in; new customer profiles follow approved onboarding."),
         requirements=(("Google OAuth switched on", "google_oauth_enabled"),
                       ("Google client ID configured", "google_client_id"),
                       ("Google client secret configured", "google_client_secret")),
         repair="Check both allowed Google callback URIs, Google Cloud OAuth consent, credentials and the KOFAD login route. Never display the client secret.",
         manage="/market/access/"),
    dict(id="gmail", group="google", name="Gmail API business sender", mark="GM",
         purpose="Uses approved company Gmail consent to send mail via HTTPS.",
         steps=("Company account grants Gmail sending consent.",
                "KOFAD stores an encrypted refresh token.",
                "An approved outgoing message uses Google's Gmail API."),
         requirements=(("Gmail API enabled", "gmail_enabled"),
                       ("Gmail API client configured", "gmail_client"),
                       ("Company sender consent exists", "gmail_consent")),
         repair="Check Gmail OAuth consent, client redirect URI and the registered company sender in My Account. A sent API response does not confirm recipient inbox delivery.",
         manage="/account/"),
    dict(id="google_drive", group="google", name="Google Drive backup storage", mark="GD",
         purpose="Company-owned cloud storage for encrypted, dated KOFAD backups.",
         steps=("KOFAD exports a full encrypted .kofad.enc archive.",
                "A company-owned Google script uploads and rechecks the file hash.",
                "Retention keeps previous successful recovery copies."),
         requirements=(("Backup export endpoint enabled", "backup_enabled"),
                       ("Backup HMAC key configured", "backup_hmac"),
                       ("Offline backup passphrase configured", "backup_passphrase")),
         repair="Check the KOFAD Drive backup folder, verify a dated .kofad.enc file, its SHA-256, and perform a controlled restore validation. Django cannot read Google Drive quota or stored files from this screen.",
         manage="/settings/backup/"),
    dict(id="apps_script", group="google", name="Google Apps Script scheduler", mark="GS",
         purpose="Runs the company Google Drive backup once each Sunday without a Railway cron service.",
         steps=("A Sunday trigger runs in the company Google account.",
                "The script signs a POST request to KOFAD's backup export.",
                "The encrypted file is verified in Drive and failures trigger company emails."),
         requirements=(("KOFAD backup endpoint enabled", "backup_enabled"),
                       ("Server signing key available", "backup_hmac")),
         repair="In Google Apps Script inspect Triggers and Executions, confirm weeklyKofadBackup runs, and check the Drive folder. The server cannot prove a future Google trigger exists.",
         manage="https://script.google.com/home"),
    dict(id="google_maps", group="google", name="Google Maps and geocoding", mark="MAP",
         purpose="Supports delivery address search, location lookup and mapping.",
         steps=("Browser uses a domain-restricted Maps key.",
                "Server geocoding uses its separate server key.",
                "Google controls quotas and billing in Cloud Console."),
         requirements=(("Restricted browser key configured", "maps_browser"),
                       ("Server geocoding key configured", "maps_server")),
         repair="Inspect key restrictions, allowed referrers, enabled APIs, quota and billing in Google Cloud. This page does not incur map API usage.",
         manage="/market-settings/"),
    dict(id="brevo", group="communications", name="Brevo transactional email", mark="BV",
         purpose="Sends approved customer, staff and transaction email using HTTPS.",
         steps=("KOFAD queues a permitted email.",
                "The worker submits it to Brevo from a verified sender.",
                "Provider delivery receipts must separately confirm delivery."),
         requirements=(("Brevo API key configured", "brevo_api"),
                       ("Verified transactional sender configured", "brevo_transaction"),
                       ("Security sender configured", "brevo_security")),
         repair="Check Brevo sender verification, free daily quota, outbox failures and provider delivery events. API acceptance is not proof of inbox delivery.",
         manage="/communications/"),
    dict(id="brevo_reporting", group="communications", name="Brevo provider event reports", mark="BR",
         purpose="Read-only authenticated reporting for sent, delivered, bounced, deferred and blocked transactional email.",
         steps=("KOFAD sends email through Brevo when configured.",
                "Brevo records events even if KOFAD has not installed its webhook.",
                "A read-only provider report checks recent event samples without sending mail."),
         requirements=(("Brevo transactional API key configured", "brevo_api"),),
         repair="Run Verify provider API. If verified, reporting works in Brevo, while KOFAD webhook sync must be diagnosed separately. For recent delivery evidence check event samples and matching KOFAD outbox.",
         manage="/communications/"),
    dict(id="brevo_webhook", group="communications", name="Brevo → KOFAD webhook receipts", mark="EV",
         purpose="Receives authenticated delivered/bounce/spam signals from Brevo.",
         steps=("Brevo posts an event to the KOFAD HTTPS webhook.",
                "A private bearer token authenticates the event.",
                "A matched message receives recorded delivery evidence."),
         requirements=(("Brevo webhook token configured", "brevo_webhook"),),
         repair="Set an approved long bearer token on both sides, confirm Brevo's webhook destination, and check recent provider delivery events. Never place tokens in URLs.",
         manage="/communications/"),
    dict(id="inbound_email", group="communications", name="Cloudflare email routing and inbox", mark="IN",
         purpose="Forwards incoming business-domain mail into KOFAD's email centre.",
         steps=("Cloudflare Email Routing accepts mail for KOFAD addresses.",
                "The authorised mail worker sends structured messages to KOFAD.",
                "The email centre stores incoming conversations and assignments."),
         requirements=(("Email centre enabled", "email_center"),
                       ("Authenticated inbound secret configured", "email_ingest")),
         repair="Check Cloudflare email routes, DNS/MX/SPF/DKIM and Worker delivery logs, then confirm a recent incoming letter. Inbound availability is not proved by an outbound email test.",
         manage="/communications/"),
    dict(id="arkesel", group="communications", name="Arkesel SMS", mark="SMS",
         purpose="Sends approved Ghana SMS notifications, reminders and alerts.",
         steps=("A business event queues an SMS.",
                "The existing SMS worker submits it to Arkesel.",
                "Delivery reports distinguish accepted, delivered and failed messages."),
         requirements=(("SMS channel enabled", "sms_enabled"),
                       ("Arkesel adapter selected", "arkesel_selected"),
                       ("Arkesel API key configured", "arkesel_api"),
                       ("Live mode (not sandbox)", "sms_live")),
         repair="Check Arkesel API key, sender approval, remaining credit, worker logs, and delivery receipts. This screen never sends a test SMS or consumes credit.",
         manage="/communications/"),
    dict(id="whatsapp", group="communications", name="Meta WhatsApp Cloud API", mark="WA",
         purpose="Handles approved WhatsApp templates, replies and webhook events.",
         steps=("KOFAD submits an approved WhatsApp template when enabled.",
                "Meta validates the business phone and sends the message.",
                "Signed webhooks supply delivery outcomes and customer replies."),
         requirements=(("WhatsApp enabled", "wa_enabled"),
                       ("Meta access token configured", "wa_token"),
                       ("Phone-number ID configured", "wa_phone"),
                       ("App secret configured", "wa_secret"),
                       ("Webhook verify token configured", "wa_verify")),
         repair="Check Meta app review, phone-number ID, approved templates, webhook subscription and token expiry. Avoid sending unsolicited test messages.",
         manage="/settings/communications/"),
    dict(id="paystack", group="payments", name="Paystack and MoMo", mark="PS",
         purpose="Handles configured customer checkout and point-of-sale MoMo payment verification.",
         steps=("KOFAD initiates a transaction only when an approved payment feature is enabled.",
                "Paystack processes the customer's chosen payment.",
                "KOFAD verifies provider status before posting a paid sale."),
         requirements=(("Paystack secret key configured", "paystack_api"),
                       ("At least one Paystack MoMo channel enabled", "paystack_enabled")),
         repair="Check Paystack dashboard, callback origin, live/test keys, last attempts and webhooks. Never mark payment as paid based only on an order redirect.",
         manage="/settings/online-payments/"),
    dict(id="hubtel", group="payments", name="Hubtel payments", mark="HT",
         purpose="Hubtel checkout and planned direct MoMo capability.",
         steps=("Hubtel API account and collection identifier must be approved.",
                "KOFAD's provider adapter can communicate when authorised.",
                "A verified provider status—not a browser return—decides payment completion."),
         requirements=(("Hubtel checkout enabled", "hubtel_enabled"),
                       ("Hubtel API identifier available", "hubtel_id"),
                       ("Hubtel API key available", "hubtel_key"),
                       ("Collection account configured", "hubtel_collection")),
         repair="Direct Hubtel MoMo is awaiting provider documentation and authorisation. This dashboard is diagnostic only: no changes to Hubtel checkout or payment behavior.",
         manage="/settings/online-payments/"),
    dict(id="paypal", group="payments", name="PayPal", mark="PP",
         purpose="Potential additional checkout method; not currently wired into KOFAD payments.",
         steps=("A merchant account alone is not an integration.",
                "Client credentials, callbacks and currency support need review.",
                "KOFAD must implement and test a provider adapter before payments can be processed."),
         requirements=(), planned=True,
         repair="No PayPal transaction integration exists in the current KOFAD code. Requires business approval, provider onboarding, Ghana availability checks and separate implementation.",
         manage="/settings/online-payments/"),
    dict(id="geoapify", group="growth", name="Geoapify Ghana geocoding", mark="GEO",
         purpose="Optional Ghana address search and reverse lookups without changing delivery-fee calculations.",
         steps=("Signed-in customers request a location search or reverse lookup.",
                "After Google geocoding if present, KOFAD may use Geoapify with a guarded daily API budget.",
                "When disabled, unavailable or out of budget, existing Nominatim fallback remains."),
         requirements=(("Geoapify search enabled", "geoapify_enabled"),
                       ("Geoapify API key installed", "geoapify_key")),
         repair="Obtain a free Geoapify API key and install KOFAD_GEOAPIFY_API_KEY in Railway. Keep KOFAD_GEOAPIFY_ENABLED=0 until a Ghana test lookup and provider quota are verified. A configured key is not connection proof.",
         manage="https://myprojects.geoapify.com/"),
    dict(id="google_analytics", group="growth", name="Google Analytics 4", mark="GA",
         purpose="Optional consent-gated website traffic analytics.",
         steps=("Visitors choose whether to allow analytics.",
                "The website loads GA4 only when consent is granted.",
                "Reports and conversion tracking are viewed in Google Analytics."),
         requirements=(("GA4 measurement ID set", "ga4_id"),),
         repair="Check GA4 property, tag installation and consent controls. This screen does not track visitors or verify the GA4 reporting API.",
         manage="https://analytics.google.com/"),
    dict(id="search_console", group="growth", name="Google Search Console", mark="SC",
         purpose="Tracks indexing, search traffic and domain verification.",
         steps=("Google validates property ownership.",
                "Googlebot discovers/indexes eligible KOFAD pages.",
                "Site health, queries and coverage appear in Search Console."),
         requirements=(("Search verification token configured", "search_verify"),),
         repair="Confirm the property is added in Google Search Console and inspect indexing reports. A DNS TXT token or page meta tag alone does not prove an active connection.",
         manage="https://search.google.com/search-console"),
    dict(id="native_push", group="growth", name="Android push notifications", mark="APP",
         purpose="Planned mobile OS notifications for the separate KOFAD Android app project.",
         steps=("Android app registration and device consent are required.",
                "Push service credentials and token lifecycle must be configured.",
                "A test device must confirm notifications are delivered."),
         requirements=(), planned=True,
         repair="Android implementation is maintained in a separate workstream. No push-provider connectivity proof is available to the Django staff portal.",
         manage="/settings/"),
)

def _configuration():
    # No values of tokens, account IDs, secrets, URLs carrying credentials, or
    # full environment variables are returned to templates or JSON responses.
    return {
        "railway_runtime": bool(os.environ.get("RAILWAY_SERVICE_ID")),
        "pwned_passwords_enabled": bool(settings.KOFAD_PWNED_PASSWORDS_ENABLED),
        "sentry_enabled": bool(settings.KOFAD_SENTRY_ENABLED),
        "sentry_dsn": bool(settings.KOFAD_SENTRY_DSN),
        "ipinfo_enabled": bool(settings.KOFAD_IPINFO_LITE_ENABLED),
        "ipinfo_token": bool(settings.KOFAD_IPINFO_LITE_TOKEN),
        "upstash_enabled": bool(settings.KOFAD_UPSTASH_REST_ENABLED),
        "upstash_url": bool(settings.KOFAD_UPSTASH_REST_URL),
        "upstash_token": bool(settings.KOFAD_UPSTASH_REST_TOKEN),
        "postgres_configured": settings.DATABASES["default"].get("ENGINE") == "django.db.backends.postgresql",
        "google_oauth_enabled": bool(settings.KOFAD_GOOGLE_OAUTH_ENABLED),
        "google_client_id": bool(settings.KOFAD_GOOGLE_CLIENT_ID),
        "google_client_secret": bool(settings.KOFAD_GOOGLE_CLIENT_SECRET),
        "gmail_enabled": bool(settings.KOFAD_GMAIL_API_ENABLED),
        "gmail_client": bool(settings.KOFAD_GMAIL_CLIENT_ID and settings.KOFAD_GMAIL_CLIENT_SECRET),
        "gmail_consent": False,  # checked against local encrypted consent row below
        "backup_enabled": bool(settings.KOFAD_GDRIVE_BACKUP_ENABLED),
        "backup_hmac": len(settings.KOFAD_GDRIVE_BACKUP_HMAC_KEY) >= 32,
        "backup_passphrase": len(settings.KOFAD_GDRIVE_BACKUP_PASSPHRASE) >= 24,
        "maps_browser": bool(settings.GOOGLE_MAPS_BROWSER_KEY and settings.GOOGLE_MAPS_BROWSER_KEY_RESTRICTED),
        "maps_server": bool(settings.GOOGLE_MAPS_SERVER_KEY),
        "geoapify_enabled": bool(settings.KOFAD_GEOAPIFY_ENABLED),
        "geoapify_key": bool(settings.KOFAD_GEOAPIFY_API_KEY),
        "brevo_api": bool(settings.KOFAD_BREVO_API_KEY),
        "brevo_transaction": bool(settings.KOFAD_BREVO_TRANSACTION_FROM_EMAIL),
        "brevo_security": bool(settings.KOFAD_BREVO_SECURITY_FROM_EMAIL),
        "brevo_webhook": len(settings.KOFAD_BREVO_WEBHOOK_TOKEN) >= 32,
        "email_center": bool(settings.KOFAD_EMAIL_CENTER_ENABLED),
        "email_ingest": len(settings.KOFAD_EMAIL_INGEST_SECRET) >= 24,
        "sms_enabled": bool(settings.SMS_ENABLED),
        "arkesel_selected": settings.SMS_PROVIDER == "arkesel",
        "arkesel_api": bool(settings.ARKESEL_API_KEY),
        "sms_live": not settings.SMS_SANDBOX,
        "wa_enabled": bool(settings.WHATSAPP_ENABLED),
        "wa_token": bool(settings.WHATSAPP_ACCESS_TOKEN),
        "wa_phone": bool(settings.WHATSAPP_PHONE_NUMBER_ID),
        "wa_secret": bool(settings.WHATSAPP_APP_SECRET),
        "wa_verify": bool(settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN),
        "paystack_api": bool(settings.PAYSTACK_SECRET_KEY),
        "paystack_enabled": bool(settings.PAYSTACK_CUSTOMER_MOMO_ENABLED or settings.PAYSTACK_POS_MOMO_ENABLED),
        "hubtel_enabled": bool(settings.HUBTEL_CHECKOUT_ENABLED),
        "hubtel_id": bool(settings.HUBTEL_API_ID),
        "hubtel_key": bool(settings.HUBTEL_API_KEY),
        "hubtel_collection": bool(settings.HUBTEL_COLLECTION_ACCOUNT),
        "ga4_id": bool(settings.KOFAD_GA4_MEASUREMENT_ID),
        "search_verify": bool(settings.GOOGLE_SITE_VERIFICATION),
    }


def _read_last(queryset, fields):
    try:
        return queryset.values(*fields).first()
    except DatabaseError:
        logger.warning("Connections view: local activity evidence query unavailable")
        return None


def _evidence(now):
    """Return safe summaries; never select or log payloads or recipient data."""
    observations = {}
    from core.email_models import EmailDeliveryEvent, EmailLetter, EmailDailyUsage
    from core.models import SmsAttempt, WhatsAppAttempt
    from marketplace.models import GmailSenderConnection, GoogleIdentity, MarketPaymentAttempt

    def register(key, row, timestamp, outcome, text):
        if row and row.get(timestamp):
            observations[key] = {
                "at": row[timestamp], "outcome": outcome(row),
                "text": text(row),
            }

    register("brevo_webhook", _read_last(
        EmailDeliveryEvent.objects.order_by("-received_at"),
        ("received_at", "event"),
    ), "received_at", lambda r: "success" if r["event"] == "delivered" else (
        "failed" if r["event"] in {"hard_bounce", "blocked", "spam", "invalid_email", "error"} else "uncertain"
    ), lambda r: "Brevo reported an authenticated " + str(r["event"]).replace("_", " ") + " event (not a general provider health test).")
    # Event rows are about specific recipients; do not show their address/ID.
    observations["brevo"] = observations.get("brevo_webhook", {}).copy() if observations.get("brevo_webhook") else {}
    if not observations["brevo"]:
        register("brevo", _read_last(
            EmailLetter.objects.filter(direction="outbound").order_by("-created_at"),
            ("created_at", "status", "delivery_status"),
        ), "created_at", lambda r: "failed" if r["status"] == "failed" else "uncertain",
        lambda r: "Latest outgoing email status: " + str(r["status"]).replace("_", " ") +
                  "; delivery " + str(r["delivery_status"]).replace("_", " ") + ".")
    register("inbound_email", _read_last(
        EmailLetter.objects.filter(direction="inbound").order_by("-created_at"),
        ("created_at", "status"),
    ), "created_at", lambda r: "success", lambda r: "An incoming business email was recorded by the KOFAD email centre.")
    register("arkesel", _read_last(
        SmsAttempt.objects.filter(provider="arkesel").order_by("-updated_at"),
        ("updated_at", "status", "error_code"),
    ), "updated_at", lambda r: "success" if r["status"] == "delivered" else (
        "failed" if r["status"] in {"failed", "rejected"} else "uncertain"
    ), lambda r: "Latest Arkesel SMS attempt: " + str(r["status"]).replace("_", " ") +
                  (" (" + str(r["error_code"]) + ")" if r["error_code"] else "") + ".")
    register("whatsapp", _read_last(
        WhatsAppAttempt.objects.order_by("-updated_at"),
        ("updated_at", "status", "error_code"),
    ), "updated_at", lambda r: "success" if r["status"] in {"delivered", "read"} else (
        "failed" if r["status"] == "failed" else "uncertain"
    ), lambda r: "Latest WhatsApp attempt: " + str(r["status"]).replace("_", " ") +
                  (" (" + str(r["error_code"]) + ")" if r["error_code"] else "") + ".")
    for key, provider in (("paystack", "paystack"), ("hubtel", "hubtel")):
        register(key, _read_last(
            MarketPaymentAttempt.objects.filter(provider=provider).order_by("-created_at"),
            ("created_at", "verified_at", "status"),
        ), "created_at", lambda r: "success" if r["verified_at"] and r["status"].lower() in {"paid", "success", "successful", "completed"} else (
            "failed" if r["status"].lower() in {"failed", "cancelled", "rejected"} else "uncertain"
        ), lambda r: "Latest recorded " + provider.title() + " attempt: " + str(r["status"]).replace("_", " ") + ".")
    consent = _read_last(GmailSenderConnection.objects.filter(pk=1), ("connected_at", "last_send_at"))
    if consent:
        observations["gmail_consent"] = {"configured": True}
        if consent["last_send_at"]:
            observations["gmail"] = {
                "at": consent["last_send_at"], "outcome": "accepted",
                "text": "Gmail API last accepted a send request; inbox delivery is not verified.",
            }
    register("google_login", _read_last(
        GoogleIdentity.objects.filter(last_login_at__isnull=False).order_by("-last_login_at"),
        ("last_login_at",), 
    ), "last_login_at", lambda r: "success",
    lambda r: "A Google-linked account completed sign-in to KOFAD.")
    # Provider quota is a local reservation counter, not a Brevo balance API.
    usage = _read_last(EmailDailyUsage.objects.filter(day=timezone.localdate()), ("attempted", "accepted", "failed", "day"))
    if usage:
        observations["brevo_usage"] = {
            "attempted": usage["attempted"], "accepted": usage["accepted"],
            "failed": usage["failed"], "limit": min(300, max(1, settings.KOFAD_BREVO_DAILY_LIMIT)),
        }
    # Bounded, grouped counters from the existing indexed business tables.
    # No customer addresses, bodies, token identifiers or payment amounts.
    cutoff = now - timedelta(days=7)
    summaries = (
        ("email_activity", EmailLetter.objects.filter(
            direction="outbound", created_at__gte=cutoff), "status"),
        ("brevo_receipts", EmailDeliveryEvent.objects.filter(
            received_at__gte=cutoff), "event"),
        ("sms_activity", SmsAttempt.objects.filter(
            started_at__gte=cutoff, provider="arkesel"), "status"),
        ("whatsapp_activity", WhatsAppAttempt.objects.filter(
            started_at__gte=cutoff), "status"),
        ("paystack_activity", MarketPaymentAttempt.objects.filter(
            provider="paystack", created_at__gte=cutoff), "status"),
        ("hubtel_activity", MarketPaymentAttempt.objects.filter(
            provider="hubtel", created_at__gte=cutoff), "status"),
    )
    for key, queryset, field in summaries:
        observations[key] = {
            str(row[field]): row["total"]
            for row in queryset.values(field).annotate(total=Count("pk"))[:20]
        }
    return observations


def catalog(probes=None):
    now = timezone.now()
    probes = probes or {}
    config = _configuration()
    # Prevent a temporary DB issue from crashing the diagnostics screen.
    try:
        observations = _evidence(now)
    except DatabaseError:
        logger.warning("Connections view: application evidence unavailable")
        observations = {}
    config["gmail_consent"] = bool(observations.get("gmail_consent", {}).get("configured"))
    rows = []
    for spec in CATALOG:
        checks = [{"label": label, "ok": config.get(key, False)} for label, key in spec["requirements"]]
        missing = [c["label"] for c in checks if not c["ok"]]
        proof = observations.get(spec["id"], {})
        state = "external" if spec.get("external") else "planned" if spec.get("planned") else "not_connected"
        details = []
        if spec.get("planned"):
            details.append("No production integration has been implemented in this app.")
        elif spec.get("external"):
            details.append("Managed outside KOFAD. Account/service health must be confirmed in the provider console.")
        elif not checks:
            state = "external"
        elif missing:
            # Explicitly enabled but missing critical credentials is an issue.
            # An intentionally disabled or not-yet-onboarded provider stays grey.
            enabled_flags = {
                "google_login": "google_oauth_enabled", "gmail": "gmail_enabled",
                "sentry": "sentry_enabled", "geoapify": "geoapify_enabled",
                "google_drive": "backup_enabled", "apps_script": "backup_enabled",
                "inbound_email": "email_center", "arkesel": "sms_enabled",
                "whatsapp": "wa_enabled", "paystack": "paystack_enabled",
                "hubtel": "hubtel_enabled",
            }
            active = config.get(enabled_flags.get(spec["id"], ""), False)
            if spec["id"] == "brevo":
                active = config["brevo_transaction"] or config["brevo_security"]
            if spec["id"] == "brevo_webhook":
                active = config["brevo_api"]
            state = "issue" if active else "not_connected"
            details.append("Incomplete prerequisites: " + "; ".join(missing) + ".")
        else:
            state = "ready"
            details.append("KOFAD configuration checks passed, but credentials have NOT been tested with the provider.")
        if proof and proof.get("at"):
            age = now - proof["at"]
            if age <= timedelta(days=7) and state == "ready":
                if proof["outcome"] == "success":
                    state = "working"
                    details.append("A relevant event completed successfully within the past seven days. This is historical evidence, not a live API probe.")
                elif proof["outcome"] == "failed":
                    state = "issue"
                    details.append("The latest recorded provider event failed; inspect logs and the provider dashboard.")
                else:
                    state = "warning"
                    details.append("The latest event does not prove delivery or payment verification.")
            elif state == "ready" and age > timedelta(days=7):
                state = "warning"
                details.append("No recent verified activity within seven days. The connection may still work, but this portal cannot prove it.")
        # Never show 'working' merely because the app can generate a backup:
        # Google's private Apps Script/Drive trigger is a different system.
        if spec["id"] in {"google_drive", "apps_script"} and state == "ready":
            state = "warning"
            details.append("Google-side successful upload, schedule and restore tests cannot be read from KOFAD's database.")
        if spec["id"] == "railway" and state == "ready":
            state = "warning"
            details.append("Local Railway runtime detected, but deployment and billing metrics require Railway's own console.")
        if spec["id"] == "postgres" and state == "ready":
            try:
                with connection.cursor() as cur:
                    cur.execute("SELECT 1")
                    if cur.fetchone()[0] == 1:
                        state = "working"
                        proof = {"at": now, "text": "Read-only database query succeeded for this dashboard request.", "outcome": "success"}
                        details.append("Live PostgreSQL SELECT 1 returned successfully.")
            except DatabaseError:
                state = "issue"
                details.append("PostgreSQL read-only query failed. Inspect database connection, status and resource limits.")
        # Brevo local-usage is informative only (not provider balance).
        metrics = []
        if spec["id"] == "brevo" and observations.get("brevo_usage"):
            usage = observations["brevo_usage"]
            metrics.append("Today: " + str(usage["attempted"]) + " attempted recipients, " +
                           str(usage["accepted"]) + " accepted, " + str(usage["failed"]) +
                           " failed (local cap " + str(usage["limit"]) + ").")
        evidence_key = {
            "brevo": "email_activity",
            "brevo_reporting": "brevo_receipts",
            "brevo_webhook": "brevo_receipts",
            "arkesel": "sms_activity",
            "whatsapp": "whatsapp_activity",
            "paystack": "paystack_activity",
            "hubtel": "hubtel_activity",
        }.get(spec["id"])
        activity = observations.get(evidence_key, {}) if evidence_key else {}
        if activity:
            metrics.append(
                "Last 7 days (KOFAD local records): " +
                " · ".join(str(key).replace("_", " ") + " " + str(value)
                           for key, value in sorted(activity.items())[:12]) + "."
            )
        elif evidence_key:
            metrics.append("No " + ("authenticated webhook receipts" if evidence_key == "brevo_receipts" else "matching local events") +
                           " recorded in KOFAD during the past seven days; absence alone does not prove a provider outage.")
        if spec["id"] == "paystack":
            metrics.append("Customer MoMo enabled: " + ("Yes" if settings.PAYSTACK_CUSTOMER_MOMO_ENABLED else "No") +
                           " · POS MoMo enabled: " + ("Yes" if settings.PAYSTACK_POS_MOMO_ENABLED else "No") + ".")
        if spec["id"] == "arkesel":
            metrics.append("Mode: " + ("Sandbox" if settings.SMS_SANDBOX else "Live") +
                           " · sender configured: " + ("Yes" if settings.SMS_SENDER_ID else "No") + ".")
        # A recent manually initiated authenticated provider check is distinct
        # from local event evidence. Results are expiring, server-side session
        # summaries, not permanent claims of provider health.
        probe = probes.get(spec["id"], {})
        if probe and not spec.get("planned"):
            verified_state = probe.get("state")
            if verified_state == "verified":
                if not missing:
                    state = "working"
                    # A successful authenticated probe supersedes the generic
                    # "not tested" message added for local-only configuration.
                    details = [
                        detail for detail in details
                        if detail != "KOFAD configuration checks passed, but credentials have NOT been tested with the provider."
                    ]
                details.append("Manually checked provider API or routing; scope is documented in the verification evidence.")
            elif verified_state == "failed":
                state = "issue"
                details.append("Latest read-only verification failed. See provider-level diagnosis below.")
            elif verified_state == "partial":
                if state not in {"issue", "not_connected"}:
                    state = "warning"
                details.append("Only part of the connection could be verified; see the exact probe scope.")
            elif verified_state == "not_configured":
                state = "not_connected"
        if spec["id"] == "brevo_webhook" and not config["brevo_webhook"] and config["brevo_api"]:
            state = "issue"
            details.append("Brevo outbound API can operate, but KOFAD has no authenticated delivery-receipt token. This does NOT imply that Brevo's independent event reporting API is unavailable.")
        repair = spec["repair"]
        if spec["id"] == "upstash_redis" and state == "working" and probe.get("state") == "verified" and not missing:
            repair = (
                "Upstash authentication is verified and the shared map-search limiter is enabled. "
                "Monitor Free-tier usage and map lookups. If Redis becomes unavailable, "
                "set KOFAD_UPSTASH_REST_ENABLED=0 on Railway and redeploy to restore local limits."
            )
        rows.append({
            **spec,
            "repair": repair,
            "status": state, "status_label": STATUS[state][0],
            "status_style": STATUS[state][1],
            "checks": checks, "missing": missing, "issues": details,
            "proof": proof if proof.get("at") else None,
            "probe": probe if probe else None,
            "metrics": metrics,
            "requirements_count": len(checks),
            "requirements_ok": len(checks) - len(missing),
        })
    counts = {state: sum(1 for row in rows if row["status"] == state) for state in STATUS}
    return {"items": rows, "counts": counts, "checked_at": now, "groups": GROUPS}
