"""
Root URL configuration for snb-backend-core.
"""

from django.contrib import admin
from django.urls import include, path

from spb_core.views import health
from courts.views import SportsCenterScheduleView, SlotDetailView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("auth/", include("auth_ext.urls")),
    path("api/players/", include("players.urls")),
    path("api/courts/", include("courts.urls")),
    # grava-3432.1 — atomic single-time booking
    path("api/bookings", include("bookings.urls")),
    # grava-3432.7 — booking series preview & create
    path("api/booking-series", include("series.urls")),
    # grava-3106.5.2 — sports center schedule
    path(
        "api/sports-centers/<str:sc_id>/schedule",
        SportsCenterScheduleView.as_view(),
        name="sports-center-schedule",
    ),
    # grava-3106.5.3 — slot detail
    path(
        "api/slots/<str:slot_id>",
        SlotDetailView.as_view(),
        name="slot-detail",
    ),
]
