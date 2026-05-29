"""
notifications.views — In-app notification HTTP endpoints (grava-52bc.2).

GET  /api/notifications?page=&limit=    — paginated list, newest first (2.5)
PATCH /api/notifications/{id}/read      — mark single notification read (2.6)
POST  /api/notifications/read-all       — mark all notifications read (2.7)

Auth: SupabaseJWTAuthentication via _authenticate_request() from players.views.
Any authenticated role (player OR owner) may access their own notifications.
"""
from __future__ import annotations

import requests
from django.http import JsonResponse, HttpResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import AuthenticationFailed

from django.conf import settings

from auth_ext.rest import user_headers

_DEFAULT_PAGE = 1
_DEFAULT_LIMIT = 20


# ---------------------------------------------------------------------------
# Auth helpers (mirrors players.views pattern)
# ---------------------------------------------------------------------------


def _authenticate_request(request):
    """
    Authenticate the request using the shared Supabase JWT decoder.
    Returns (SupabaseUser, token) or None when no token; raises AuthenticationFailed.
    """
    from auth_ext.middleware import _decode_token
    from auth_ext.authentication import SupabaseUser

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[len("Bearer "):]
    if not token:
        return None

    payload = _decode_token(token)
    if payload is None:
        raise AuthenticationFailed("Invalid or expired token.")

    uid = payload.get("sub")
    if not uid:
        raise AuthenticationFailed("Token is missing the 'sub' claim.")

    app_metadata = payload.get("app_metadata") or {}
    role = app_metadata.get("role") or "authenticated"

    return SupabaseUser(uid=uid, role=role, token=token), token


def _get_auth_user(request):
    """
    Authenticate request; return (user, None) on success or (None, JsonResponse) on failure.
    Accepts any authenticated role (player or owner).
    """
    try:
        result = _authenticate_request(request)
    except AuthenticationFailed as exc:
        return None, JsonResponse({"error": str(exc.detail)}, status=401)

    if result is None:
        return None, JsonResponse(
            {"error": "Authentication credentials were not provided."}, status=401
        )

    user, _token = result
    return user, None


def _rest_base() -> str:
    return getattr(settings, "SUPABASE_URL", "")


def _format_notification(row: dict) -> dict:
    """Normalise a Supabase notification row for the API response."""
    return {
        "id": row.get("id"),
        "user_id": row.get("user_id"),
        "type": row.get("type"),
        "title": row.get("title"),
        "body": row.get("body"),
        "data": row.get("data") or {},
        "read": row.get("read", False),
        "related_booking_id": row.get("related_booking_id"),
        "related_series_id": row.get("related_series_id"),
        "created_at": row.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@method_decorator(csrf_exempt, name="dispatch")
class NotificationsListView(View):
    """
    GET /api/notifications?page=<int>&limit=<int>
    Returns paginated notifications for the authenticated user, newest first.
    (grava-52bc.2.5)
    """

    def get(self, request):
        user, err = _get_auth_user(request)
        if err is not None:
            return err

        # Parse pagination params
        try:
            page = max(1, int(request.GET.get("page", _DEFAULT_PAGE)))
        except (ValueError, TypeError):
            page = _DEFAULT_PAGE
        try:
            limit = max(1, min(100, int(request.GET.get("limit", _DEFAULT_LIMIT))))
        except (ValueError, TypeError):
            limit = _DEFAULT_LIMIT

        offset = (page - 1) * limit

        supabase_url = _rest_base()
        notif_endpoint = f"{supabase_url}/rest/v1/notifications"
        headers = user_headers(user.token)
        # Use Supabase range-based pagination
        headers["Range"] = f"{offset}-{offset + limit - 1}"
        headers["Range-Unit"] = "items"
        headers["Prefer"] = "return=representation,count=exact"

        try:
            resp = requests.get(
                notif_endpoint,
                params={
                    "user_id": f"eq.{user.id}",
                    "order": "created_at.desc",
                    "limit": str(limit),
                    "offset": str(offset),
                },
                headers=headers,
                timeout=10,
            )
        except requests.RequestException:
            return JsonResponse({"error": "Notifications service unavailable."}, status=503)

        if resp.status_code not in (200, 206):
            return JsonResponse({"error": "Notifications service unavailable."}, status=503)

        rows = resp.json()
        results = [_format_notification(r) for r in rows]

        return JsonResponse(
            {
                "page": page,
                "limit": limit,
                "results": results,
            },
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class NotificationsMarkReadView(View):
    """
    PATCH /api/notifications/{id}/read
    Marks a single notification as read for the authenticated user.
    Only updates if user_id matches — prevents cross-user writes.
    (grava-52bc.2.6)
    """

    def patch(self, request, notif_id: str):
        user, err = _get_auth_user(request)
        if err is not None:
            return err

        supabase_url = _rest_base()
        notif_endpoint = f"{supabase_url}/rest/v1/notifications"
        headers = user_headers(user.token)

        try:
            resp = requests.patch(
                notif_endpoint,
                params={
                    "id": f"eq.{notif_id}",
                    "user_id": f"eq.{user.id}",
                },
                json={"read": True},
                headers=headers,
                timeout=10,
            )
        except requests.RequestException:
            return JsonResponse({"error": "Notifications service unavailable."}, status=503)

        if resp.status_code not in (200, 204):
            return JsonResponse({"error": "Notifications service unavailable."}, status=503)

        rows = resp.json() if resp.status_code == 200 else []
        if not rows:
            return JsonResponse({"error": "Notification not found."}, status=404)

        return JsonResponse(_format_notification(rows[0]), status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class NotificationsReadAllView(View):
    """
    POST /api/notifications/read-all
    Marks all unread notifications as read for the authenticated user.
    (grava-52bc.2.7)
    """

    def post(self, request):
        user, err = _get_auth_user(request)
        if err is not None:
            return err

        supabase_url = _rest_base()
        notif_endpoint = f"{supabase_url}/rest/v1/notifications"
        headers = user_headers(user.token)
        headers["Prefer"] = "return=minimal"

        try:
            resp = requests.patch(
                notif_endpoint,
                params={
                    "user_id": f"eq.{user.id}",
                    "read": "eq.false",
                },
                json={"read": True},
                headers=headers,
                timeout=10,
            )
        except requests.RequestException:
            return JsonResponse({"error": "Notifications service unavailable."}, status=503)

        if resp.status_code not in (200, 204):
            return JsonResponse({"error": "Notifications service unavailable."}, status=503)

        return JsonResponse({"status": "ok"}, status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)
