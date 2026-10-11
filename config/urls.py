from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from core.telegram_views import webhook as telegram_webhook
from core.telegram_portal import staff_portal as telegram_staff_portal
from core.telegram_recovery import staff_recovery as telegram_staff_recovery

urlpatterns = [
    path("telegram/webhook/", telegram_webhook, name="telegram_webhook"),
    path("integrations/telegram/", telegram_staff_portal, name="telegram_staff_portal"),
    path("integrations/telegram/recover/", telegram_staff_recovery, name="telegram_staff_recovery"),
    path(settings.STAFF_LOGIN_SLUG + "/technical-admin/", admin.site.urls),
    path("", include("marketplace.urls")),
    path("", include("core.urls")),
]
