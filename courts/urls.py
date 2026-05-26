from django.urls import path
from .views import CourtsListView, CourtDetailView

app_name = "courts"

urlpatterns = [
    path("", CourtsListView.as_view(), name="courts-list"),
    path("<str:court_id>/", CourtDetailView.as_view(), name="courts-detail"),
]
