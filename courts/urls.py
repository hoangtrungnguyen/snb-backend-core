from django.urls import path
from .views import CourtsListView, CourtDetailView, SlotsView, RecurrenceView

app_name = "courts"

urlpatterns = [
    path("", CourtsListView.as_view(), name="courts-list"),
    path("slots", SlotsView.as_view(), name="slots-create"),
    path("<str:court_id>/recurrence", RecurrenceView.as_view(), name="recurrence-create"),
    path("<str:court_id>/", CourtDetailView.as_view(), name="courts-detail"),
]
