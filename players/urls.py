from django.urls import path
from .views import PlayersFcmTokenView, PlayersMeAvatarView, PlayersMeView

urlpatterns = [
    path("me/avatar", PlayersMeAvatarView.as_view(), name="players-me-avatar"),
    path("me/fcm-token", PlayersFcmTokenView.as_view(), name="players-me-fcm-token"),
    path("me", PlayersMeView.as_view(), name="players-me"),
]
