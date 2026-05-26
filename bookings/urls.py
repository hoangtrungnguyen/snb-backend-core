from django.urls import path

from .views import BookingCreateView, ManualBookingView

app_name = "bookings"

urlpatterns = [
    # POST /api/bookings — atomic single-time booking (grava-3432.1)
    path("", BookingCreateView.as_view(), name="bookings-create"),
    # POST /api/bookings/manual — manual / walk-in booking (grava-3432.2)
    path("/manual", ManualBookingView.as_view(), name="bookings-manual"),
]
