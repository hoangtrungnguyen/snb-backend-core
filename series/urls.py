from django.urls import path

from .views import (
    BookingSeriesPreviewView,
    BookingSeriesCreateView,
    BookingSeriesDetailView,
    BookingSeriesStatusView,
)

app_name = "series"

urlpatterns = [
    # POST /api/booking-series/preview — preview recurring occurrences (grava-3432.7.1)
    path("/preview", BookingSeriesPreviewView.as_view(), name="booking-series-preview"),
    # POST /api/booking-series — create booking series (grava-3432.7.5)
    path("", BookingSeriesCreateView.as_view(), name="booking-series-create"),
    # GET /api/booking-series/<id>/status — series status transitions (grava-3432.8)
    # NOTE: this must come before the detail route to avoid <series_id> matching "status"
    path("/<str:series_id>/status", BookingSeriesStatusView.as_view(), name="booking-series-status"),
    # GET /api/booking-series/<id> — series detail with occurrences (grava-3432.8)
    path("/<str:series_id>", BookingSeriesDetailView.as_view(), name="booking-series-detail"),
]
