from django.urls import path

from .views import (
    BookingCreateView,
    BookingDetailView,
    BookingListView,
    BookingStatusView,
    ManualBookingView,
    PriceEstimateView,
)

app_name = "bookings"

urlpatterns = [
    # POST /api/bookings — atomic single-time booking (grava-3432.1)
    path("", BookingCreateView.as_view(), name="bookings-create"),
    # POST /api/bookings/manual — manual / walk-in booking (grava-3432.2)
    path("/manual", ManualBookingView.as_view(), name="bookings-manual"),
    # GET  /api/bookings/price-estimate — price calculation service (grava-3432.6)
    path("/price-estimate", PriceEstimateView.as_view(), name="bookings-price-estimate"),
    # PATCH /api/bookings/<booking_id>/status — status transitions (grava-3432.3)
    path("/<str:booking_id>/status", BookingStatusView.as_view(), name="bookings-status"),
    # GET  /api/bookings/list — booking list & search (grava-3432.4)
    path("/list", BookingListView.as_view(), name="bookings-list"),
    # GET  /api/bookings/<id> — booking detail (grava-3432.4)
    path("/<str:booking_id>", BookingDetailView.as_view(), name="bookings-detail"),
]
