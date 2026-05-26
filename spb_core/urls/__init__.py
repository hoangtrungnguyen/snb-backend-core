"""
Root URL configuration for snb-backend-core.
"""

from django.contrib import admin
from django.urls import include, path

from spb_core.views import dashboard, health
from courts.views import (
    SportsCenterScheduleView,
    SlotDetailView,
    SlotAccessView,
    SlotJoinView,
    SlotJoinRequestApproveView,
    SlotJoinRequestRejectView,
    SlotParticipantsView,
    SlotJoinStatusView,
    SlotLastMinuteView,
    OpenSlotsForJoinView,
)

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("auth/", include("auth_ext.urls")),
    path("api/players/", include("players.urls")),
    path("api/courts/", include("courts.urls")),
    # grava-3432.1 — atomic single-time booking
    path("api/bookings", include("bookings.urls")),
    # grava-3432.7 — booking series preview & create
    path("api/booking-series", include("series.urls")),
    # grava-52bc.2 — in-app notification dispatch
    path("api/notifications", include("notifications.urls")),
    # grava-3106.5.2 — sports center schedule
    path(
        "api/sports-centers/<str:sc_id>/schedule",
        SportsCenterScheduleView.as_view(),
        name="sports-center-schedule",
    ),
    # grava-5044.3 — open slot list for join (must come BEFORE the <slot_id> catch-all)
    path(
        "api/slots/open-for-join",
        OpenSlotsForJoinView.as_view(),
        name="slots-open-for-join",
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
    # grava-52bc.4 — last-minute slot push notification
    path(
        "api/slots/<str:slot_id>/last-minute",
        SlotLastMinuteView.as_view(),
        name="slot-last-minute",
    ),
]
