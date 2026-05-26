from django.urls import path

from .views import BookingCreateView, BookingStatusView, ManualBookingView

app_name = "bookings"

urlpatterns = [
    # POST /api/bookings — atomic single-time booking (grava-3432.1)
    path("", BookingCreateView.as_view(), name="bookings-create"),
    # POST /api/bookings/manual — manual / walk-in booking (grava-3432.2)
    path("/manual", ManualBookingView.as_view(), name="bookings-manual"),
    # PATCH /api/bookings/<booking_id>/status — status transitions (grava-3432.3)
    path("/<str:booking_id>/status", BookingStatusView.as_view(), name="bookings-status"),
]
