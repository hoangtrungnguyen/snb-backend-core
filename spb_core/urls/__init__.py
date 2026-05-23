"""
Root URL configuration for snb-backend-core.

Each Django app owns its own urls.py; they are included here under a versioned
API prefix so the public contract remains stable as apps evolve.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    # API v1 — app-level URL confs included below
    path("api/v1/courts/", include("courts.urls", namespace="courts")),
    path("api/v1/bookings/", include("bookings.urls", namespace="bookings")),
    path("api/v1/series/", include("series.urls", namespace="series")),
    path("api/v1/auth/", include("auth_ext.urls", namespace="auth_ext")),
    path("api/v1/notifications/", include("notifications.urls", namespace="notifications")),
    path("api/v1/analytics/", include("analytics.urls", namespace="analytics")),
]
