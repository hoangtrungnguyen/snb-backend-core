from django.urls import path
from .views import PlayersFcmTokenView, PlayersMeAvatarView, PlayersMeView, PlayersMeLocationView

urlpatterns = [
    path("me/avatar", PlayersMeAvatarView.as_view(), name="players-me-avatar"),
    path("me/fcm-token", PlayersFcmTokenView.as_view(), name="players-me-fcm-token"),
    # grava-5044.4 — player location update (before me catch-all)
    path("me/location", PlayersMeLocationView.as_view(), name="players-me-location"),
    path("me", PlayersMeView.as_view(), name="players-me"),
]
