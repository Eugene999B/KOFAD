from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.STAFF_LOGIN_SLUG + "/technical-admin/", admin.site.urls),
    path("", include("marketplace.urls")),
    path("", include("core.urls")),
]
