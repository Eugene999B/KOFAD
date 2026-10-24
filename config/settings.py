import os
import re
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if len(SECRET_KEY) < 32:
    raise RuntimeError("Set DJANGO_SECRET_KEY to at least 32 random characters.")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
CSRF_TRUSTED_ORIGINS = [s for s in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if s]
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "core",
    "marketplace.apps.MarketplaceConfig", "hubtel_direct.apps.HubtelDirectConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "core.domain_middleware.OfficialDomainMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware", "core.middleware.RequestSizeLimitMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.AccessMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"], "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages", "core.context.shell", "core.cookie_preferences.context",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": dj_database_url.config(conn_max_age=60, conn_health_checks=True)}
if not DATABASES["default"]:
    raise RuntimeError("DATABASE_URL is required. PostgreSQL is required for transaction safety.")
if DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
    raise RuntimeError("Use PostgreSQL; SQLite does not provide the required row locks.")
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "core.password_validation.PwnedPasswordValidator"},
]
# Free, keyless HIBP range lookup is opt-in for controlled production activation.
KOFAD_PWNED_PASSWORDS_ENABLED = os.environ.get("KOFAD_PWNED_PASSWORDS_ENABLED", "0") == "1"
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "Africa/Accra"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STAFF_LOGIN_SLUG = os.environ.get("STAFF_LOGIN_SLUG", "login").strip("/")
if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,80}", STAFF_LOGIN_SLUG):
    raise RuntimeError("STAFF_LOGIN_SLUG must contain only lowercase letters, digits and hyphens.")
STAFF_LOGIN_PATH = f"/{STAFF_LOGIN_SLUG}/"

# Invisible Cloudflare Turnstile. Off until both login widgets and secrets
# have been provisioned; when on, missing/failed Siteverify rejects login.
KOFAD_TURNSTILE_ENABLED = os.environ.get("KOFAD_TURNSTILE_ENABLED", "0") == "1"
KOFAD_TURNSTILE_MARKET_SITE_KEY = os.environ.get("KOFAD_TURNSTILE_MARKET_SITE_KEY", "").strip()
KOFAD_TURNSTILE_MARKET_SECRET_KEY = os.environ.get("KOFAD_TURNSTILE_MARKET_SECRET_KEY", "").strip()
KOFAD_TURNSTILE_STAFF_SITE_KEY = os.environ.get("KOFAD_TURNSTILE_STAFF_SITE_KEY", "").strip()
KOFAD_TURNSTILE_STAFF_SECRET_KEY = os.environ.get("KOFAD_TURNSTILE_STAFF_SECRET_KEY", "").strip()
PUBLIC_SITE_ORIGIN = os.environ.get("PUBLIC_SITE_ORIGIN", "https://kofadimpex.com").rstrip("/")
MARKET_SITE_ORIGIN = os.environ.get("MARKET_SITE_ORIGIN", "https://market.kofadimpex.com").rstrip("/")
GOOGLE_SITE_VERIFICATION = os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip()
# Approved native app release destinations; empty until externally signed/published.
# Two physically independent app identities and version channels.
for _native_kind in ("CUSTOMER", "STAFF"):
    _native_prefix = "KOFAD_" + _native_kind + "_APP_"
    for _native_field in ("VERSION", "ANDROID_URL", "IOS_URL", "WINDOWS_URL"):
        globals()[_native_prefix + _native_field] = os.environ.get(
            _native_prefix + _native_field, ""
        ).strip()

LOGIN_URL = STAFF_LOGIN_PATH
LOGIN_REDIRECT_URL = "/workspace/"
LOGOUT_REDIRECT_URL = STAFF_LOGIN_PATH
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_DOMAIN = None
SESSION_COOKIE_PATH = "/"
SESSION_COOKIE_NAME = "sessionid" if DEBUG else "__Host-kofad_session"
CSRF_COOKIE_DOMAIN = None
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
STAFF_SESSION_SECONDS = int(os.environ.get("STAFF_SESSION_SECONDS", "43200"))  # 12 hours
# Public SHOW- items must be independently verified before sale. Never delete
# historical showcase transactions to clean up a storefront.
KOFAD_REQUIRE_SHOWCASE_VERIFICATION = os.environ.get(
    "KOFAD_REQUIRE_SHOWCASE_VERIFICATION", "1"
) != "0"
KOFAD_VERIFIED_SHOWCASE_SKUS = tuple(
    item.strip().upper() for item in
    os.environ.get("KOFAD_VERIFIED_SHOWCASE_SKUS", "").split(",") if item.strip()
)
MARKET_SESSION_SECONDS = int(os.environ.get("MARKET_SESSION_SECONDS", "7200"))  # 2 hours
# Opt-in Google Apps Script weekly off-site export (not a Railway scheduled job).
# Passphrase MUST be escrowed offline; losing Railway's secret or changing the
# Django signing key without a recovery plan can make archived files unusable.
KOFAD_GDRIVE_BACKUP_ENABLED = os.environ.get("KOFAD_GDRIVE_BACKUP_ENABLED", "0") == "1"
KOFAD_GDRIVE_BACKUP_HMAC_KEY = os.environ.get("KOFAD_GDRIVE_BACKUP_HMAC_KEY", "")
KOFAD_GDRIVE_BACKUP_PASSPHRASE = os.environ.get("KOFAD_GDRIVE_BACKUP_PASSPHRASE", "")

# Versioned signature keys for full KOFAD backups. These are distinct from the
# archive's AES-GCM passphrase and Django session/signing SECRET_KEY.
# Blank means legacy signature mode for backwards-compatible rollout.
KOFAD_BACKUP_SIGNING_KEY = os.environ.get("KOFAD_BACKUP_SIGNING_KEY", "")
KOFAD_BACKUP_SIGNING_KEY_ID = os.environ.get("KOFAD_BACKUP_SIGNING_KEY_ID", "backup-v1")
# Private JSON map of retired key IDs to signing keys. A reserved "legacy"
# entry can verify pre-migration archives after DJANGO_SECRET_KEY rotation.
KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON = os.environ.get(
    "KOFAD_BACKUP_PREVIOUS_SIGNING_KEYS_JSON", ""
)

# Password guessing guard: account lockout (5 failures) is independent of
# browser cookies. A physical network peer may be a shared CDN proxy; set a
# higher per-peer budget to limit cross-account attacks without immediately
# locking every staff member behind that proxy.
KOFAD_LOGIN_SOURCE_MAX_FAILURES = max(
    20, min(200, int(os.environ.get("KOFAD_LOGIN_SOURCE_MAX_FAILURES", "50")))
)
PRIVILEGED_MFA_ENFORCED = os.environ.get("PRIVILEGED_MFA_ENFORCED", "0") == "1"
MFA_SESSION_SECONDS = int(os.environ.get("MFA_SESSION_SECONDS", str(STAFF_SESSION_SECONDS)))
SESSION_COOKIE_AGE = max(STAFF_SESSION_SECONDS, MARKET_SESSION_SECONDS)
# Fixed staff/customer deadlines are enforced separately. Read-only polling must
# not overwrite session changes made by concurrent requests (e.g. backup readiness).
SESSION_SAVE_EVERY_REQUEST = False
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "1" if not DEBUG else "0") == "1"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
DATA_UPLOAD_MAX_MEMORY_SIZE = 26214400
LOGGING = {"version": 1, "disable_existing_loggers": False, "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "INFO"}}

SECURE_REDIRECT_EXEMPT = [r"^health/$"]

# Staff digest email is OFF until verified SMTP/DNS, recipients and consent are configured.
EMAIL_AUTOMATIONS_ENABLED = os.environ.get("EMAIL_AUTOMATIONS_ENABLED", "0") == "1"
EMAIL_DELIVERY_ENABLED = os.environ.get("EMAIL_DELIVERY_ENABLED", "0") == "1"
# SMTP settings are declared once below using KOFAD_SMTP_* variables.

# Staff SMS is INDEPENDENT of existing management-contact SMS.
SMS_STAFF_NOTICES_ENABLED = os.environ.get("SMS_STAFF_NOTICES_ENABLED", "0") == "1"
SMS_STAFF_DAILY_MAX_RECIPIENTS = int(os.environ.get("SMS_STAFF_DAILY_MAX_RECIPIENTS", "4"))
NOTIFICATION_CRITICAL_VARIANCE_GHS = os.environ.get("NOTIFICATION_CRITICAL_VARIANCE_GHS", "500")

# SMS credentials belong in deployment variables, never in database exports or the browser.
SMS_ENABLED = os.environ.get("SMS_ENABLED","0") == "1"
SMS_PROVIDER = os.environ.get("SMS_PROVIDER","arkesel")
SMS_SENDER_ID = os.environ.get("SMS_SENDER_ID","Kofad Impex").strip()
SMS_SANDBOX = os.environ.get("SMS_SANDBOX","1") == "1"
SMS_PUBLIC_ORIGIN = os.environ.get("SMS_PUBLIC_ORIGIN","").rstrip("/")
ARKESEL_API_KEY = os.environ.get("ARKESEL_API_KEY","")
SMS_TIMEOUT_SECONDS = 15
SMS_MAX_ATTEMPTS = 3
SMS_ADAPTERS = {"arkesel":"core.sms.providers.Arkesel"}

# Meta WhatsApp Cloud API. Secrets stay in deployment variables.
WHATSAPP_ENABLED = os.environ.get("WHATSAPP_ENABLED", "0") == "1"
WHATSAPP_PUBLIC_ORIGIN = os.environ.get("WHATSAPP_PUBLIC_ORIGIN", "").rstrip("/")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.environ.get("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_BUSINESS_ACCOUNT_ID = os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
WHATSAPP_GRAPH_VERSION = os.environ.get("WHATSAPP_GRAPH_VERSION", "v25.0")
WHATSAPP_TIMEOUT_SECONDS = int(os.environ.get("WHATSAPP_TIMEOUT_SECONDS", "20"))
WHATSAPP_WEBHOOK_MAX_BYTES = int(os.environ.get("WHATSAPP_WEBHOOK_MAX_BYTES", "524288"))
WHATSAPP_WEBHOOK_RETENTION_DAYS = int(os.environ.get("WHATSAPP_WEBHOOK_RETENTION_DAYS", "30"))
WHATSAPP_BOT_REPLY_RETENTION_DAYS = int(os.environ.get("WHATSAPP_BOT_REPLY_RETENTION_DAYS", "90"))



# Public KOFAD Market. Secrets remain server-side deployment variables.
CUSTOMER_OTP_ENABLED = os.environ.get("CUSTOMER_OTP_ENABLED", "0") == "1"
CUSTOMER_OTP_GLOBAL_HOURLY_LIMIT = int(os.environ.get("CUSTOMER_OTP_GLOBAL_HOURLY_LIMIT", "60"))
CUSTOMER_OTP_SESSION_HOURLY_LIMIT = int(os.environ.get("CUSTOMER_OTP_SESSION_HOURLY_LIMIT", "6"))
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_TIMEOUT_SECONDS = int(os.environ.get("PAYSTACK_TIMEOUT_SECONDS", "20"))
PAYSTACK_POS_MOMO_ENABLED = os.environ.get("PAYSTACK_POS_MOMO_ENABLED", "0") == "1"
MARKET_IMAGE_MAX_BYTES = int(os.environ.get("MARKET_IMAGE_MAX_BYTES", "26214400"))
MARKET_RESERVATION_MINUTES = int(os.environ.get("MARKET_RESERVATION_MINUTES", "20"))
GOOGLE_MAPS_SERVER_KEY = os.environ.get("GOOGLE_MAPS_SERVER_KEY", "")
GOOGLE_MAPS_BROWSER_KEY = os.environ.get("GOOGLE_MAPS_BROWSER_KEY", "")
GOOGLE_MAPS_BROWSER_KEY_RESTRICTED = os.environ.get("GOOGLE_MAPS_BROWSER_KEY_RESTRICTED", "0") == "1"
MAP_LOOKUP_SESSION_MINUTE_LIMIT = int(os.environ.get("MAP_LOOKUP_SESSION_MINUTE_LIMIT", "40"))
MAP_LOOKUP_GLOBAL_MINUTE_LIMIT = int(os.environ.get("MAP_LOOKUP_GLOBAL_MINUTE_LIMIT", "500"))
GOOGLE_MAPS_MAP_ID = os.environ.get("GOOGLE_MAPS_MAP_ID", "")
GOOGLE_MAPS_TIMEOUT_SECONDS = int(os.environ.get("GOOGLE_MAPS_TIMEOUT_SECONDS", "12"))
# Geoapify complements Ghana address search without changing delivery pricing.
# Free-tier daily budget is capped server-side; disabled until key is verified.
KOFAD_GEOAPIFY_ENABLED = os.environ.get("KOFAD_GEOAPIFY_ENABLED", "0") == "1"
KOFAD_GEOAPIFY_API_KEY = os.environ.get("KOFAD_GEOAPIFY_API_KEY", "").strip()

# Optional published-product semantic discovery on the existing Qdrant free cluster.
# Never index private customer, stock, payment or accounting records.
KOFAD_QDRANT_ENABLED = os.environ.get("KOFAD_QDRANT_ENABLED", "0") == "1"
KOFAD_QDRANT_URL = os.environ.get("KOFAD_QDRANT_URL", "").strip()
KOFAD_QDRANT_API_KEY = os.environ.get("KOFAD_QDRANT_API_KEY", "").strip()

# Optional mirror for NEW publicly uploaded product photos only.
# The original compressed Django database image remains as a fallback.
KOFAD_CLOUDINARY_ENABLED = os.environ.get("KOFAD_CLOUDINARY_ENABLED", "0") == "1"
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY", "").strip()
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET", "").strip()

# Existing Hubtel Online Checkout, unchanged. New Direct APIs are separately gated.
HUBTEL_API_ID = os.environ.get("HUBTEL_API_ID", "").strip()
HUBTEL_API_KEY = os.environ.get("HUBTEL_API_KEY", "").strip()
HUBTEL_COLLECTION_ACCOUNT = os.environ.get("HUBTEL_COLLECTION_ACCOUNT", "").strip()
HUBTEL_TIMEOUT_SECONDS = int(os.environ.get("HUBTEL_TIMEOUT_SECONDS", "10"))
HUBTEL_CHECKOUT_ENABLED = os.environ.get("HUBTEL_CHECKOUT_ENABLED", "0") == "1"
# Separate September 2026 Hubtel Direct receive/disbursement APIs. All OFF until
# Hubtel confirms the fixed outbound IP, API scopes, audited payouts and merchant UAT.
HUBTEL_DISBURSEMENT_ACCOUNT = os.environ.get("HUBTEL_DISBURSEMENT_ACCOUNT", "").strip()
HUBTEL_DIRECT_EGRESS_CONFIRMED = os.environ.get("HUBTEL_DIRECT_EGRESS_CONFIRMED", "0") == "1"
HUBTEL_DIRECT_RECONCILE_TOKEN = os.environ.get("HUBTEL_DIRECT_RECONCILE_TOKEN", "")
HUBTEL_DIRECT_RECEIVE_ENABLED = os.environ.get("HUBTEL_DIRECT_RECEIVE_ENABLED", "0") == "1"
HUBTEL_DIRECT_SEND_MOMO_ENABLED = os.environ.get("HUBTEL_DIRECT_SEND_MOMO_ENABLED", "0") == "1"
HUBTEL_DIRECT_SEND_BANK_ENABLED = os.environ.get("HUBTEL_DIRECT_SEND_BANK_ENABLED", "0") == "1"
HUBTEL_DIRECT_MAX_PAYOUT_GHS = os.environ.get("HUBTEL_DIRECT_MAX_PAYOUT_GHS", "500.00")
HUBTEL_DIRECT_DAILY_PAYOUT_GHS = os.environ.get("HUBTEL_DIRECT_DAILY_PAYOUT_GHS", "2000.00")

# New consented Hubtel financial operations. Keep disabled until verified
# merchant scopes, static web egress, UAT and second-person finance approvals.
HUBTEL_DEBT_PREAPPROVAL_ENABLED = os.environ.get("HUBTEL_DEBT_PREAPPROVAL_ENABLED", "0") == "1"
HUBTEL_DEBT_AUTO_DEBIT_ENABLED = os.environ.get("HUBTEL_DEBT_AUTO_DEBIT_ENABLED", "0") == "1"
HUBTEL_REFUNDS_ENABLED = os.environ.get("HUBTEL_REFUNDS_ENABLED", "0") == "1"
HUBTEL_PAYROLL_DISBURSEMENTS_ENABLED = os.environ.get("HUBTEL_PAYROLL_DISBURSEMENTS_ENABLED", "0") == "1"
HUBTEL_DEBT_DAILY_DEBIT_CAP_GHS = os.environ.get("HUBTEL_DEBT_DAILY_DEBIT_CAP_GHS", "1000.00")



# Independent kill switch. Enable only after verified-number configuration and a delivery test.
WHATSAPP_BOT_ENABLED = os.environ.get("WHATSAPP_BOT_ENABLED", "0") == "1"
# Staff invitation WhatsApp requires a separate Meta-approved template with one URL placeholder.
KOFAD_STAFF_INVITE_WHATSAPP_TEMPLATE = os.environ.get("KOFAD_STAFF_INVITE_WHATSAPP_TEMPLATE", "").strip()
KOFAD_STAFF_INVITE_WHATSAPP_LANGUAGE = os.environ.get("KOFAD_STAFF_INVITE_WHATSAPP_LANGUAGE", "en").strip()

PAYSTACK_CUSTOMER_MOMO_ENABLED = os.environ.get("PAYSTACK_CUSTOMER_MOMO_ENABLED", "0") == "1"


# Optional email verification and notifications. Disabled until merchant-domain
# SMTP credentials and SPF/DKIM/DMARC are validated by the administrator.
KOFAD_EMAIL_ENABLED = os.environ.get("KOFAD_EMAIL_ENABLED", "0") == "1"
KOFAD_EMAIL_NOTIFICATIONS_ENABLED = os.environ.get("KOFAD_EMAIL_NOTIFICATIONS_ENABLED", "0") == "1"
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("KOFAD_SMTP_HOST", "")
EMAIL_PORT = int(os.environ.get("KOFAD_SMTP_PORT", "587"))
KOFAD_SMTP_SECURITY = os.environ.get(
    "KOFAD_SMTP_SECURITY", "ssl" if EMAIL_PORT == 465 else "starttls"
).strip().lower()
if KOFAD_SMTP_SECURITY not in {"ssl", "starttls"}:
    raise RuntimeError("KOFAD_SMTP_SECURITY must be 'ssl' or 'starttls'.")
EMAIL_USE_SSL = KOFAD_SMTP_SECURITY == "ssl"
EMAIL_USE_TLS = KOFAD_SMTP_SECURITY == "starttls"
EMAIL_HOST_USER = os.environ.get("KOFAD_SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("KOFAD_SMTP_PASSWORD", "")
EMAIL_TIMEOUT = 12
DEFAULT_FROM_EMAIL = os.environ.get("KOFAD_FROM_EMAIL", "")


# Google OIDC is separate from Gmail SMTP. Keep disabled until a Google Cloud
# Web OAuth client has both canonical KOFAD callback URIs allowlisted.
KOFAD_GOOGLE_OAUTH_ENABLED = os.environ.get("KOFAD_GOOGLE_OAUTH_ENABLED", "0") == "1"
KOFAD_GOOGLE_CLIENT_ID = os.environ.get("KOFAD_GOOGLE_CLIENT_ID", "").strip()
KOFAD_GOOGLE_CLIENT_SECRET = os.environ.get("KOFAD_GOOGLE_CLIENT_SECRET", "").strip()
KOFAD_STAFF_SITE_ORIGIN = os.environ.get(
    "KOFAD_STAFF_SITE_ORIGIN", "https://staff.kofadimpex.com"
).rstrip("/")
KOFAD_MARKET_SITE_ORIGIN = os.environ.get(
    "KOFAD_MARKET_SITE_ORIGIN", "https://market.kofadimpex.com"
).rstrip("/")


# Railways Hobby does not permit SMTP. Use Gmail's HTTPS API for the
# designated business sender, authorised by company management via OAuth.
# This is independent of public/customer Google sign-in credentials.
KOFAD_GMAIL_API_ENABLED = os.environ.get("KOFAD_GMAIL_API_ENABLED", "0") == "1"
KOFAD_GMAIL_CLIENT_ID = os.environ.get("KOFAD_GMAIL_CLIENT_ID", "").strip()
KOFAD_GMAIL_CLIENT_SECRET = os.environ.get("KOFAD_GMAIL_CLIENT_SECRET", "").strip()


# Provider-neutral KOFAD transactional email setup:
# "gmail_api" for authorised company Gmail OAuth over HTTPS,
# "brevo" for Brevo's larger free email allowance using HTTPS,
# "smtp" for environments where SMTP is permitted.
KOFAD_EMAIL_PROVIDER = os.environ.get("KOFAD_EMAIL_PROVIDER", "auto").strip().lower()
KOFAD_BREVO_API_KEY = os.environ.get("KOFAD_BREVO_API_KEY", "").strip()
# Conservative KOFAD-wide daily cap for Brevo HTTPS sending (enforced in database).
KOFAD_BREVO_DAILY_LIMIT = int(os.environ.get("KOFAD_BREVO_DAILY_LIMIT", "300"))
# Business senders confirmed as active in Brevo (others use the verified transactional sender).
KOFAD_BREVO_REGISTERED_SENDERS = os.environ.get("KOFAD_BREVO_REGISTERED_SENDERS", "transactions@kofadimpex.com")
KOFAD_BREVO_SECURITY_FROM_EMAIL = os.environ.get("KOFAD_BREVO_SECURITY_FROM_EMAIL", "").strip()
KOFAD_BREVO_TRANSACTION_FROM_EMAIL = os.environ.get("KOFAD_BREVO_TRANSACTION_FROM_EMAIL", "").strip()
KOFAD_SUPPORT_REPLY_TO_EMAIL = os.environ.get("KOFAD_SUPPORT_REPLY_TO_EMAIL", "").strip()
# Feature remains private until inbound Worker and HTTPS outbound sender are verified.
KOFAD_EMAIL_CENTER_ENABLED = os.environ.get("KOFAD_EMAIL_CENTER_ENABLED", "0") == "1"
KOFAD_EMAIL_INGEST_SECRET = os.environ.get("KOFAD_EMAIL_INGEST_SECRET", "").strip()
KOFAD_BREVO_WEBHOOK_TOKEN = os.environ.get("KOFAD_BREVO_WEBHOOK_TOKEN", "").strip()
KOFAD_BREVO_WEBHOOK_AUTO_REGISTER = os.environ.get("KOFAD_BREVO_WEBHOOK_AUTO_REGISTER", "0").strip() == "1"
# Optional scoped Cloudflare READ-ONLY token for manual diagnostics; do not
# use a Global API key or an edit/write token. Never shown in staff pages.
KOFAD_CLOUDFLARE_READ_TOKEN = os.environ.get("KOFAD_CLOUDFLARE_READ_TOKEN", "").strip()
KOFAD_CLOUDFLARE_ZONE_ID = os.environ.get("KOFAD_CLOUDFLARE_ZONE_ID", "5459e449118218037a186d47a9fe7fd7").strip()


# Optional public-site analytics is loaded only after the visitor opts in.
KOFAD_GA4_MEASUREMENT_ID = os.environ.get("KOFAD_GA4_MEASUREMENT_ID", "").strip()
# PostHog receives only consented, server-forwarded public page categories.
# No browser SDK, person identifiers, private routes, session replay or autocapture.
# Disabled until the EU PostHog project token is installed and validated.
KOFAD_POSTHOG_ENABLED = os.environ.get("KOFAD_POSTHOG_ENABLED", "0") == "1"
KOFAD_POSTHOG_PROJECT_TOKEN = os.environ.get("KOFAD_POSTHOG_PROJECT_TOKEN", "").strip()

# Native account authorization remains OFF until verified app deep links and
# hardware-backed OS token storage are shipped and reviewed.
KOFAD_NATIVE_AUTH_ENABLED = os.environ.get("KOFAD_NATIVE_AUTH_ENABLED", "0") == "1"

# Telegram is opt-in and OFF by default until reviewed and configured.
TELEGRAM_BOT_ENABLED = os.environ.get("TELEGRAM_BOT_ENABLED", "0") == "1"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
TELEGRAM_WEBHOOK_BASE_URL = os.environ.get("TELEGRAM_WEBHOOK_BASE_URL", "https://staff.kofadimpex.com").strip().rstrip("/")
TELEGRAM_WEBHOOK_MAX_BYTES = 65536

# Enterprise Telegram features disabled independently; never require DeepSeek.
TELEGRAM_ACCOUNT_LINK_ENABLED = os.environ.get("TELEGRAM_ACCOUNT_LINK_ENABLED", "0") == "1"
TELEGRAM_BUSINESS_ANALYTICS_ENABLED = os.environ.get("TELEGRAM_BUSINESS_ANALYTICS_ENABLED", "0") == "1"
TELEGRAM_NOTIFICATIONS_ENABLED = os.environ.get("TELEGRAM_NOTIFICATIONS_ENABLED", "0") == "1"
TELEGRAM_RECOVERY_ENABLED = os.environ.get("TELEGRAM_RECOVERY_ENABLED", "0") == "1"

# Three independent Telegram bots: existing official bot retains OTP/security.
# New bot usernames must be the EXACT names issued by BotFather, not guesses.
TELEGRAM_CUSTOMER_BOT_USERNAME = os.environ.get("TELEGRAM_CUSTOMER_BOT_USERNAME", "").strip().lstrip("@")
TELEGRAM_STAFF_BOT_USERNAME = os.environ.get("TELEGRAM_STAFF_BOT_USERNAME", "").strip().lstrip("@")
TELEGRAM_CUSTOMER_BOT_TOKEN = os.environ.get("TELEGRAM_CUSTOMER_BOT_TOKEN", "").strip()
TELEGRAM_STAFF_BOT_TOKEN = os.environ.get("TELEGRAM_STAFF_BOT_TOKEN", "").strip()
TELEGRAM_CUSTOMER_WEBHOOK_SECRET = os.environ.get("TELEGRAM_CUSTOMER_WEBHOOK_SECRET", "").strip()
TELEGRAM_STAFF_WEBHOOK_SECRET = os.environ.get("TELEGRAM_STAFF_WEBHOOK_SECRET", "").strip()
TELEGRAM_CUSTOMER_BOT_ENABLED = os.environ.get("TELEGRAM_CUSTOMER_BOT_ENABLED", "0") == "1"
TELEGRAM_STAFF_BOT_ENABLED = os.environ.get("TELEGRAM_STAFF_BOT_ENABLED", "0") == "1"

# KOFAD public AI assistant. Disabled until the user supplies their own key.
# Model is hard-coded in marketplace/kofad_ai.py to OpenRouter's $0 free router;
# never accept a paid model ID, dynamic fallback or customer-supplied model.
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "").strip()
KOFAD_OPENROUTER_FREE_ENABLED = os.environ.get("KOFAD_OPENROUTER_FREE_ENABLED", "0") == "1"

# Direct Gemini uses a fixed free-eligible text model. Operator must verify the
# linked Google project is on its no-billing free tier before enabling.
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
KOFAD_GEMINI_ENABLED = os.environ.get("KOFAD_GEMINI_ENABLED", "0") == "1"
KOFAD_GEMINI_FREE_TIER_CONFIRMED = os.environ.get("KOFAD_GEMINI_FREE_TIER_CONFIRMED", "0") == "1"

# Staff assistant: private evidence always remains local. When enabled, free
# models see only approved generic intent words and can choose read-only tools.
# No customer data, private business figures or conversation are forwarded.
KOFAD_STAFF_AI_PLANNER_ENABLED = os.environ.get("KOFAD_STAFF_AI_PLANNER_ENABLED", "0") == "1"

# Owner-only IPinfo Lite country/ASN investigation; never a login-blocking signal.
# Both switches must be present. The token remains only in Railway secrets.
KOFAD_IPINFO_LITE_ENABLED = os.environ.get("KOFAD_IPINFO_LITE_ENABLED", "0") == "1"
KOFAD_IPINFO_LITE_TOKEN = os.environ.get("KOFAD_IPINFO_LITE_TOKEN", "").strip()

# Optional shared Redis REST map-search counter; disabled until a dedicated
# Upstash database is provisioned, credentials secured in Railway, and verified.
# Never put REST tokens in templates, client JavaScript or source control.
KOFAD_UPSTASH_REST_ENABLED = os.environ.get("KOFAD_UPSTASH_REST_ENABLED", "0") == "1"
KOFAD_UPSTASH_REST_URL = os.environ.get("KOFAD_UPSTASH_REST_URL", "").strip()
KOFAD_UPSTASH_REST_TOKEN = os.environ.get("KOFAD_UPSTASH_REST_TOKEN", "").strip()

# Privacy-minimized server-side error telemetry. Off until an approved Sentry
# project DSN is installed in Railway; no browser replay or performance traces.
KOFAD_SENTRY_ENABLED = os.environ.get("KOFAD_SENTRY_ENABLED", "0") == "1"
KOFAD_SENTRY_DSN = os.environ.get("KOFAD_SENTRY_DSN", "").strip()
KOFAD_SENTRY_SAMPLE_RATE = os.environ.get("KOFAD_SENTRY_SAMPLE_RATE", "0.25")
if KOFAD_SENTRY_ENABLED and KOFAD_SENTRY_DSN:
    from core.sentry_monitoring import configure_sentry

    configure_sentry(
        KOFAD_SENTRY_DSN,
        environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME", "production"),
        release=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
        sample_rate=KOFAD_SENTRY_SAMPLE_RATE,
    )
