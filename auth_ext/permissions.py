"""
auth_ext.permissions — DRF permission classes for role-based access control.

Provides:
- ``IsOwner``: grants access only to authenticated users whose role is "owner".
- ``IsPlayer``: grants access only to authenticated users whose role is "player".
- ``IsCourtOwner``: grants access only when ``courts.owner_id`` matches
  ``request.user.id`` for the court identified by the URL kwarg ``court_id``.
  Looks up the court via the Supabase REST API (PostgREST).
- ``IsSeriesOwner``: grants access when ``booking_series.user_id`` matches
  ``request.user.id`` (the player who made the booking) OR when
  ``courts.owner_id`` matches ``request.user.id`` (the court owner can manage
  bookings on their court). Identified by the URL kwarg ``series_id``.

All classes extend ``rest_framework.permissions.BasePermission``.  DRF returns
a ``403 Forbidden`` response whenever ``has_permission`` returns ``False``.

Usage example::

    from auth_ext.authentication import SupabaseJWTAuthentication
    from auth_ext.permissions import IsOwner, IsCourtOwner, IsSeriesOwner

    class VenueView(APIView):
        authentication_classes = [SupabaseJWTAuthentication]
        permission_classes = [IsOwner]

    class CourtUpdateView(APIView):
        authentication_classes = [SupabaseJWTAuthentication]
        permission_classes = [IsCourtOwner]

    class BookingSeriesView(APIView):
        authentication_classes = [SupabaseJWTAuthentication]
        permission_classes = [IsSeriesOwner]

Unauthenticated requests (where ``request.user.is_authenticated`` is ``False``,
e.g. Django's ``AnonymousUser``) are also denied with ``403``.  This is
intentional: the authentication class (``SupabaseJWTAuthentication``) already
handles ``401 Unauthorized`` for malformed or missing tokens; these permission
classes only run after authentication has succeeded or been skipped.

``IsCourtOwner`` denial policy (anti-enumeration):
- Court not found → 403 (same response as permission denied, leaks no info)
- Network error fetching court → 403 (deny on uncertainty, do not crash)
- ``court_id`` missing from URL kwargs → 403

``IsSeriesOwner`` denial policy (anti-enumeration):
- Series not found → 403 (leaks no info about series existence)
- Network error fetching series or court → 403 (deny on uncertainty, do not crash)
- ``series_id`` missing from URL kwargs → 403
- Non-list Supabase response (e.g. error dict) → 403, not crash
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
        anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")

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


class IsSeriesOwner(BasePermission):
    """
    Allow access when the authenticated user either created the booking series OR
    owns the court on which the series is booked.

    The permission class:

    1. Reads ``series_id`` from ``request.parser_context['kwargs']['series_id']``.
    2. Fetches the booking series from Supabase:
       ``GET {SUPABASE_URL}/rest/v1/booking_series?id=eq.{series_id}&select=user_id,court_id``
    3. If ``booking_series.user_id == request.user.id`` → grant access immediately.
    4. Otherwise fetches the court from Supabase:
       ``GET {SUPABASE_URL}/rest/v1/courts?id=eq.{court_id}&select=owner_id``
    5. If ``courts.owner_id == request.user.id`` → grant access.
    6. Any other outcome → deny.

    Denial policy (anti-enumeration — all deny cases return the same 403):
    - ``series_id`` missing from URL kwargs → 403.
    - User is not authenticated → 403 (Supabase is never called).
    - Booking series not found (empty response) → 403, not 404.
    - Network/HTTP error fetching series or court → 403 (deny on uncertainty, no crash).
    - Non-list Supabase response (e.g. error dict) → 403, not crash.
    - Neither ``user_id`` nor ``owner_id`` matches ``request.user.id`` → 403.
    """

    message = "You do not have permission to access this booking series."

    def has_permission(self, request, view) -> bool:
        # --- 1. Authentication guard (fast path, no network call) ---
        if not (request.user and request.user.is_authenticated):
            return False

        # --- 2. Extract series_id from URL kwargs ---
        kwargs = (request.parser_context or {}).get("kwargs", {})
        series_id = kwargs.get("series_id")
        if not series_id:
            logger.warning("IsSeriesOwner: series_id not found in URL kwargs")
            return False

        user_id = getattr(request.user, "id", None)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {anon_key}",
        }

        # --- 3. Fetch booking_series from Supabase REST API ---
        encoded_series_id = quote(str(series_id), safe="")
        series_url = (
            f"{supabase_url}/rest/v1/booking_series"
            f"?id=eq.{encoded_series_id}&select=user_id,court_id"
        )

        try:
            series_resp = requests.get(series_url, headers=headers, timeout=5)
            series_data = series_resp.json()
        except requests.RequestException as exc:
            logger.warning(
                "IsSeriesOwner: network error fetching booking_series %s: %s",
                series_id,
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001 — JSON decode error, etc.
            logger.warning(
                "IsSeriesOwner: unexpected error fetching booking_series %s: %s",
                series_id,
                exc,
            )
            return False

        # --- 4. Series not found or non-list response → 403 (anti-enumeration) ---
        if not isinstance(series_data, list) or not series_data:
            logger.info(
                "IsSeriesOwner: booking_series %s not found or unexpected response type: %r",
                series_id,
                type(series_data).__name__,
            )
            return False

        series_row = series_data[0]
        series_user_id = series_row.get("user_id")
        court_id = series_row.get("court_id")

        # --- 5. Fast path: user is the player who created the series ---
        if user_id and series_user_id and series_user_id == user_id:
            return True

        # --- 6. Slow path: check if user owns the court ---
        if not court_id:
            logger.warning("IsSeriesOwner: booking_series %s has no court_id", series_id)
            return False

        encoded_court_id = quote(str(court_id), safe="")
        court_url = (
            f"{supabase_url}/rest/v1/courts"
            f"?id=eq.{encoded_court_id}&select=owner_id"
        )

        try:
            court_resp = requests.get(court_url, headers=headers, timeout=5)
            court_data = court_resp.json()
        except requests.RequestException as exc:
            logger.warning(
                "IsSeriesOwner: network error fetching court %s for series %s: %s",
                court_id,
                series_id,
                exc,
            )
            return False
        except Exception as exc:  # noqa: BLE001 — JSON decode error, etc.
            logger.warning(
                "IsSeriesOwner: unexpected error fetching court %s for series %s: %s",
                court_id,
                series_id,
                exc,
            )
            return False

        # --- 7. Court not found or non-list response → 403 (anti-enumeration) ---
        if not isinstance(court_data, list) or not court_data:
            logger.info(
                "IsSeriesOwner: court %s not found or unexpected response type: %r",
                court_id,
                type(court_data).__name__,
            )
            return False

        # --- 8. Compare court owner_id to current user id ---
        owner_id = court_data[0].get("owner_id")
        return bool(owner_id and user_id and owner_id == user_id)
