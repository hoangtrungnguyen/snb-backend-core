from django.urls import path

from .views import BookingSeriesPreviewView, BookingSeriesCreateView

app_name = "series"

urlpatterns = [
    # POST /api/booking-series/preview — preview recurring occurrences (grava-3432.7.1)
    path("/preview", BookingSeriesPreviewView.as_view(), name="booking-series-preview"),
    # POST /api/booking-series — create booking series (grava-3432.7.5)
    path("", BookingSeriesCreateView.as_view(), name="booking-series-create"),
]
