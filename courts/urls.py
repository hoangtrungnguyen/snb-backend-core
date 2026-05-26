from django.urls import path
from .views import CourtsListView, CourtDetailView, SlotsView

app_name = "courts"

urlpatterns = [
    path("", CourtsListView.as_view(), name="courts-list"),
    path("slots", SlotsView.as_view(), name="slots-create"),
    path("<str:court_id>/", CourtDetailView.as_view(), name="courts-detail"),
]
