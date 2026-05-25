from django.urls import path
from .views import OwnerLoginView, OwnerForgotPasswordView, TokenRefreshView, PlayerSignupView, AuthCallbackView, PlayerLoginView, PlayerForgotPasswordView, PlayerResendVerificationView

urlpatterns = [
    path("owner/login", OwnerLoginView.as_view(), name="owner-login"),
    path("owner/forgot-password", OwnerForgotPasswordView.as_view(), name="owner-forgot-password"),
    path("refresh", TokenRefreshView.as_view(), name="token-refresh"),
    path("player/signup", PlayerSignupView.as_view(), name="player-signup"),
    path("callback", AuthCallbackView.as_view(), name="auth-callback"),
    path("player/login", PlayerLoginView.as_view(), name="player-login"),
    path("player/forgot-password", PlayerForgotPasswordView.as_view(), name="player-forgot-password"),
    path("player/resend-verification", PlayerResendVerificationView.as_view(), name="player-resend-verification"),
]
