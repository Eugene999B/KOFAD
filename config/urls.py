from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core.views import login_view
from marketplace import seo

urlpatterns = [
    path("robots.txt", seo.robots, name="robots"),
    path("sitemap.xml", seo.sitemap, name="sitemap"),
    path("admin/", RedirectView.as_view(pattern_name="administration", permanent=False)),
    path("technical-admin/login/", login_view),
    path("technical-admin/", admin.site.urls),
    path("", include("marketplace.urls")),
    path("", include("core.urls")),
]
