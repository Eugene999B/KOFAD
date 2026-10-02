from django.contrib import admin
from django.urls import include, path
from core.views import login_view
urlpatterns = [path("admin/login/", login_view), path("admin/", admin.site.urls), path("", include("core.urls"))]
