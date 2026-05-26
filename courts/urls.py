from django.urls import path
from .views import (
    CourtsListView,
    CourtDetailView,
    SlotsView,
    SlotBlockView,
    SlotUnblockView,
    RecurrenceView,
    CourtSlotsRangeView,
)

app_name = "courts"

urlpatterns = [
    path("", CourtsListView.as_view(), name="courts-list"),
    path("slots", SlotsView.as_view(), name="slots-create"),
    path("slots/<str:slot_id>/block", SlotBlockView.as_view(), name="slots-block"),
    path("slots/<str:slot_id>/unblock", SlotUnblockView.as_view(), name="slots-unblock"),
    path("<str:court_id>/recurrence", RecurrenceView.as_view(), name="recurrence-create"),
    path("<str:court_id>/slots", CourtSlotsRangeView.as_view(), name="court-slots-range"),
    path("<str:court_id>/", CourtDetailView.as_view(), name="courts-detail"),
]
