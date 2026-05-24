from django.urls import path
from .views import PlayersMeView

urlpatterns = [
    path("me", PlayersMeView.as_view(), name="players-me"),
]
