"""
auth_ext.permissions — DRF permission classes for role-based access control.

Provides:
- ``IsOwner``: grants access only to authenticated users whose role is "owner".
- ``IsPlayer``: grants access only to authenticated users whose role is "player".

Both classes extend ``rest_framework.permissions.BasePermission``.  DRF returns
a ``403 Forbidden`` response whenever ``has_permission`` returns ``False``.

Usage example::

    from auth_ext.authentication import SupabaseJWTAuthentication
    from auth_ext.permissions import IsOwner

    class VenueView(APIView):
        authentication_classes = [SupabaseJWTAuthentication]
        permission_classes = [IsOwner]

Unauthenticated requests (where ``request.user.is_authenticated`` is ``False``,
e.g. Django's ``AnonymousUser``) are also denied with ``403``.  This is
intentional: the authentication class (``SupabaseJWTAuthentication``) already
handles ``401 Unauthorized`` for malformed or missing tokens; these permission
classes only run after authentication has succeeded or been skipped.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission


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
