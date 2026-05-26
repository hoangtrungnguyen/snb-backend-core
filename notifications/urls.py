"""
URL configuration for notifications app (grava-52bc.2).

Routes:
  GET  /api/notifications               — paginated list (2.5)
  PATCH /api/notifications/{id}/read    — mark single read (2.6)
  POST  /api/notifications/read-all     — mark all read (2.7)
"""
from django.urls import path

from .views import (
    NotificationsListView,
    NotificationsMarkReadView,
    NotificationsReadAllView,
)

app_name = "notifications"

urlpatterns = [
    # POST /api/notifications/read-all must come before /{id}/read
    # to prevent 'read-all' being matched as a notification ID.
    path("/read-all", NotificationsReadAllView.as_view(), name="notifications-read-all"),
    path("/<str:notif_id>/read", NotificationsMarkReadView.as_view(), name="notifications-mark-read"),
    path("", NotificationsListView.as_view(), name="notifications-list"),
]
