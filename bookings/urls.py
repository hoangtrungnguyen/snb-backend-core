from django.urls import path

from .views import BookingCreateView, WalkInBookingView

app_name = "bookings"

urlpatterns = [
    # POST /api/bookings — atomic single-time booking (grava-3432.1)
    path("", BookingCreateView.as_view(), name="bookings-create"),
    # POST /api/bookings/walk-in — manual / walk-in booking (grava-3432.2)
    path("/walk-in", WalkInBookingView.as_view(), name="bookings-walk-in"),
]
