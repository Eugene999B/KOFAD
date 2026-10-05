import os
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
    "marketplace.apps.MarketplaceConfig",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware",
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
        "django.contrib.messages.context_processors.messages", "core.context.shell",
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
]
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
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/workspace/"
LOGOUT_REDIRECT_URL = "/login/"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
STAFF_SESSION_SECONDS = int(os.environ.get("STAFF_SESSION_SECONDS", "43200"))  # 12 hours
MARKET_SESSION_SECONDS = int(os.environ.get("MARKET_SESSION_SECONDS", "7200"))  # 2 hours
SESSION_COOKIE_AGE = max(STAFF_SESSION_SECONDS, MARKET_SESSION_SECONDS)
SESSION_SAVE_EVERY_REQUEST = True
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

# SMS credentials belong in deployment variables, never in database exports or the browser.
SMS_ENABLED = os.environ.get("SMS_ENABLED","0") == "1"
SMS_PROVIDER = os.environ.get("SMS_PROVIDER","arkesel")
SMS_SENDER_ID = os.environ.get("SMS_SENDER_ID","KOFAD")
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



# Public KOFAD Market. Secrets remain server-side deployment variables.
CUSTOMER_OTP_ENABLED = os.environ.get("CUSTOMER_OTP_ENABLED", "0") == "1"
PAYSTACK_SECRET_KEY = os.environ.get("PAYSTACK_SECRET_KEY", "")
PAYSTACK_TIMEOUT_SECONDS = int(os.environ.get("PAYSTACK_TIMEOUT_SECONDS", "20"))
MARKET_IMAGE_MAX_BYTES = int(os.environ.get("MARKET_IMAGE_MAX_BYTES", "26214400"))
MARKET_RESERVATION_MINUTES = int(os.environ.get("MARKET_RESERVATION_MINUTES", "20"))
GOOGLE_MAPS_SERVER_KEY = os.environ.get("GOOGLE_MAPS_SERVER_KEY", "")
GOOGLE_MAPS_BROWSER_KEY = os.environ.get("GOOGLE_MAPS_BROWSER_KEY", "")
GOOGLE_MAPS_MAP_ID = os.environ.get("GOOGLE_MAPS_MAP_ID", "")
GOOGLE_MAPS_TIMEOUT_SECONDS = int(os.environ.get("GOOGLE_MAPS_TIMEOUT_SECONDS", "12"))
