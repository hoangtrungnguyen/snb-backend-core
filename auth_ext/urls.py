from django.urls import path
from .views import OwnerLoginView, OwnerForgotPasswordView, TokenRefreshView, PlayerSignupView

urlpatterns = [
    path("owner/login", OwnerLoginView.as_view(), name="owner-login"),
    path("owner/forgot-password", OwnerForgotPasswordView.as_view(), name="owner-forgot-password"),
    path("refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("player/signup", PlayerSignupView.as_view(), name="player-signup"),
]
