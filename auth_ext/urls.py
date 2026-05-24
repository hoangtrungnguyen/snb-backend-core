from django.urls import path
from .views import OwnerLoginView, OwnerForgotPasswordView

urlpatterns = [
    path("owner/login", OwnerLoginView.as_view(), name="owner-login"),
    path("owner/forgot-password", OwnerForgotPasswordView.as_view(), name="owner-forgot-password"),
]
