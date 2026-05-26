"""
Root URL configuration for snb-backend-core.
"""

from django.contrib import admin
from django.urls import include, path

from spb_core.views import health

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("auth/", include("auth_ext.urls")),
    path("api/players/", include("players.urls")),
]
