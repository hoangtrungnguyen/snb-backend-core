"""
Root URL configuration for snb-backend-core.
"""

from django.contrib import admin
from django.urls import include, path

from spb_core.views import health
from courts.views import (
    SportsCenterScheduleView,
    SlotDetailView,
    SlotAccessView,
    SlotJoinView,
    SlotJoinRequestApproveView,
    SlotJoinRequestRejectView,
    SlotParticipantsView,
    SlotJoinStatusView,
)

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
    # grava-3432.5 — Play-together access control
    path(
        "api/slots/<str:slot_id>/access",
        SlotAccessView.as_view(),
        name="slot-access",
    ),
    path(
        "api/slots/<str:slot_id>/join",
        SlotJoinView.as_view(),
        name="slot-join",
    ),
    path(
        "api/slots/<str:slot_id>/participants",
        SlotParticipantsView.as_view(),
        name="slot-participants",
    ),
    path(
        "api/slots/<str:slot_id>/join-status",
        SlotJoinStatusView.as_view(),
        name="slot-join-status",
    ),
    path(
        "api/slot-join-requests/<str:join_request_id>/approve",
        SlotJoinRequestApproveView.as_view(),
        name="slot-join-request-approve",
    ),
    path(
        "api/slot-join-requests/<str:join_request_id>/reject",
        SlotJoinRequestRejectView.as_view(),
        name="slot-join-request-reject",
    ),
]
