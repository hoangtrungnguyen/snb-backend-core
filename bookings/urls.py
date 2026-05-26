from django.urls import path

from .views import BookingCreateView, ManualBookingView, BookingListView, BookingDetailView

app_name = "bookings"

urlpatterns = [
    # POST /api/bookings — atomic single-time booking (grava-3432.1)
    path("", BookingCreateView.as_view(), name="bookings-create"),
    # POST /api/bookings/manual — manual / walk-in booking (grava-3432.2)
    path("/manual", ManualBookingView.as_view(), name="bookings-manual"),
    # GET  /api/bookings/list — booking list & search (grava-3432.4)
    path("/list", BookingListView.as_view(), name="bookings-list"),
    # GET  /api/bookings/<id> — booking detail (grava-3432.4)
    path("/<str:booking_id>", BookingDetailView.as_view(), name="bookings-detail"),
]
