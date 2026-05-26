from django.urls import path
from .views import PlayersMeAvatarView, PlayersMeView

urlpatterns = [
    path("me/avatar", PlayersMeAvatarView.as_view(), name="players-me-avatar"),
    path("me", PlayersMeView.as_view(), name="players-me"),
]
