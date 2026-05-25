"""
auth_ext.permissions — DRF permission classes for role-based access control.

Provides:
- ``IsOwner``: grants access only to authenticated users whose role is "owner".
- ``IsPlayer``: grants access only to authenticated users whose role is "player".
- ``IsCourtOwner``: grants access only when ``courts.owner_id`` matches
  ``request.user.id`` for the court identified by the URL kwarg ``court_id``.
  Looks up the court via the Supabase REST API (PostgREST).

All classes extend ``rest_framework.permissions.BasePermission``.  DRF returns
a ``403 Forbidden`` response whenever ``has_permission`` returns ``False``.

Usage example::

    from auth_ext.authentication import SupabaseJWTAuthentication
    from auth_ext.permissions import IsOwner, IsCourtOwner

    class VenueView(APIView):
        authentication_classes = [SupabaseJWTAuthentication]
        permission_classes = [IsOwner]

    class CourtUpdateView(APIView):
        authentication_classes = [SupabaseJWTAuthentication]
        permission_classes = [IsCourtOwner]

Unauthenticated requests (where ``request.user.is_authenticated`` is ``False``,
e.g. Django's ``AnonymousUser``) are also denied with ``403``.  This is
intentional: the authentication class (``SupabaseJWTAuthentication``) already
handles ``401 Unauthorized`` for malformed or missing tokens; these permission
classes only run after authentication has succeeded or been skipped.

``IsCourtOwner`` denial policy (anti-enumeration):
- Court not found → 403 (same response as permission denied, leaks no info)
- Network error fetching court → 403 (deny on uncertainty, do not crash)
- ``court_id`` missing from URL kwargs → 403
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import requests
from django.conf import settings
from rest_framework.permissions import BasePermission

logger = logging.getLogger(__name__)


class IsOwner(BasePermission):
    """
    Allow access only to authenticated users with ``role == "owner"``.

    Returns ``False`` (→ HTTP 403) for:
    - Unauthenticated requests (``request.user.is_authenticated`` is ``False``).
    - Authenticated users whose ``role`` is anything other than ``"owner"``.
    """

    message = "You must be an owner to perform this action."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "owner"
        )


class IsPlayer(BasePermission):
    """
    Allow access only to authenticated users with ``role == "player"``.

    Returns ``False`` (→ HTTP 403) for:
    - Unauthenticated requests (``request.user.is_authenticated`` is ``False``).
    - Authenticated users whose ``role`` is anything other than ``"player"``.
    """

    message = "You must be a player to perform this action."

    def has_permission(self, request, view) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "role", None) == "player"
        )


class IsCourtOwner(BasePermission):
    """
    Allow access only when the authenticated user owns the court identified by
    the ``court_id`` URL kwarg.

    The permission class:

    1. Reads ``court_id`` from ``request.parser_context['kwargs']['court_id']``.
    2. Fetches the court from Supabase:
       ``GET {SUPABASE_URL}/rest/v1/courts?id=eq.{court_id}&select=owner_id``
    3. Compares ``courts.owner_id`` to ``request.user.id``.
    4. Returns ``True`` only when they match.

    Denial policy (anti-enumeration — all deny cases return the same 403):
    - ``court_id`` missing from URL kwargs → 403.
    - User is not authenticated → 403 (Supabase is never called).
    - Court not found (empty response) → 403, not 404.
    - Network/HTTP error fetching court → 403 (deny on uncertainty, no crash).
    - ``owner_id`` does not match ``request.user.id`` → 403.
    """

    message = "You do not have permission to modify this court."

    def has_permission(self, request, view) -> bool:
        # --- 1. Authentication guard (fast path, no network call) ---
        if not (request.user and request.user.is_authenticated):
            return False

        # --- 2. Extract court_id from URL kwargs ---
        kwargs = (request.parser_context or {}).get("kwargs", {})
        court_id = kwargs.get("court_id")
        if not court_id:
            logger.warning("IsCourtOwner: court_id not found in URL kwargs")
            return False

        # --- 3. Fetch court from Supabase REST API ---
        supabase_url = getattr(settings, "SUPABASE_URL", "")
        anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

        # URL-encode the court_id to prevent query injection
        encoded_court_id = quote(str(court_id), safe="")
        url = f"{supabase_url}/rest/v1/courts?id=eq.{encoded_court_id}&select=owner_id"
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
        }

        try:
            response = requests.get(url, headers=headers, timeout=5)
            data = response.json()
        except requests.RequestException as exc:
            logger.warning("IsCourtOwner: network error fetching court %s: %s", court_id, exc)
            return False
        except Exception as exc:  # noqa: BLE001 — JSON decode error, etc.
            logger.warning("IsCourtOwner: unexpected error fetching court %s: %s", court_id, exc)
            return False

        # --- 4. Court not found or non-list response → 403 (anti-enumeration) ---
        if not isinstance(data, list) or not data:
            logger.info(
                "IsCourtOwner: court %s not found or unexpected response type: %r",
                court_id,
                type(data).__name__,
            )
            return False

        # --- 5. Compare owner_id to current user id ---
        owner_id = data[0].get("owner_id")
        user_id = getattr(request.user, "id", None)
        return bool(owner_id and user_id and owner_id == user_id)
