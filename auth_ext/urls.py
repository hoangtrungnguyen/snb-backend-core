from django.urls import path
from .views import OwnerLoginView

urlpatterns = [
    path("owner/login", OwnerLoginView.as_view(), name="owner-login"),
]
