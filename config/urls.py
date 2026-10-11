from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from core.telegram_views import webhook as telegram_webhook

urlpatterns = [
    path("telegram/webhook/", telegram_webhook, name="telegram_webhook"),
    path(settings.STAFF_LOGIN_SLUG + "/technical-admin/", admin.site.urls),
    path("", include("marketplace.urls")),
    path("", include("core.urls")),
]
