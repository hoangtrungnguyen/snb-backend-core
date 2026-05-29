"""
auth_ext.authentication — Supabase JWT authentication for Django REST Framework.

Implements SupabaseJWTAuthentication(BaseAuthentication), which:

1. Reads the ``Authorization: Bearer <token>`` header.
2. Fetches the Supabase JWKS from ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``
   and decodes / validates the JWT using python-jose.
3. Fetches the user row from the ``users`` table via the Supabase REST API.
4. Returns ``(SupabaseUser, token)`` on success.
5. Returns ``None`` when no token is present (so other auth backends can run).
6. Raises ``AuthenticationFailed`` for invalid/expired tokens or unknown users.
7. Raises ``AuthenticationFailed`` (with a 503-friendly message) on JWKS network errors.
"""

from __future__ import annotations

import logging

import requests
from jose import ExpiredSignatureError, JWTError, jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from auth_ext.middleware import _decode_token  # noqa: E402 — shared JWT decoder

logger = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "


class SupabaseUser:
    """
    Lightweight authenticated-user object attached to ``request.user``.

    Attributes
    ----------
    id : str
        Supabase UID (UUID string) — value of the ``sub`` JWT claim.
    role : str
        Role string fetched from the ``users`` table (e.g. ``"player"``,
        ``"coach"``, ``"admin"``).
    is_authenticated : bool
        Always ``True`` for instances of this class.
    """

    is_authenticated: bool = True

    def __init__(self, *, uid: str, role: str, token: str | None = None) -> None:
        self.id = uid
        self.role = role
        #: The raw Supabase JWT this user authenticated with. Used to build
        #: RLS-mode PostgREST requests (see ``auth_ext.rest.user_headers``).
        self.token = token

    def __str__(self) -> str:  # pragma: no cover
        return f"SupabaseUser(id={self.id}, role={self.role})"

    def __repr__(self) -> str:  # pragma: no cover
        return self.__str__()


class SupabaseJWTAuthentication(BaseAuthentication):
    """
    DRF authentication class that validates Supabase JWTs.

    Flow
    ----
    authenticate(request)
        → extract Bearer token from Authorization header
        → _decode_jwt(token)      — fetch JWKS, verify signature/expiry
        → extract ``sub`` claim
        → _fetch_user_from_db(uid) — Supabase REST GET /users?id=eq.<uid>
        → return (SupabaseUser, token) or raise AuthenticationFailed
    """

    # --------------------------------------------------------------------------
    # Public DRF interface
    # --------------------------------------------------------------------------

    def authenticate(self, request):
        """
        Return ``(SupabaseUser, token)`` or ``None`` or raise
        ``AuthenticationFailed``.
        """
        token = self._extract_token(request)
        if token is None:
            return None  # No credentials — let other backends run

        # --- Decode & validate JWT ----------------------------------------
        try:
            payload = self._decode_jwt(token)
        except (ExpiredSignatureError, JWTError) as exc:
            raise AuthenticationFailed(f"Invalid or expired token: {exc}") from exc
        except requests.RequestException as exc:
            logger.error("JWKS fetch failed: %s", exc)
            raise AuthenticationFailed(
                "Authentication service temporarily unavailable. Please try again."
            ) from exc

        # --- Extract sub claim -------------------------------------------
        uid = payload.get("sub")
        if not uid:
            raise AuthenticationFailed("Token is missing the 'sub' claim.")

        # --- Fetch user from database -------------------------------------
        db_user = self._fetch_user_from_db(uid)
        if db_user is None:
            raise AuthenticationFailed("User not found.")

        user = SupabaseUser(uid=uid, role=db_user.get("role", "authenticated"), token=token)
        return user, token

    def authenticate_header(self, request):  # pragma: no cover
        """Return WWW-Authenticate header value for 401 responses."""
        return 'Bearer realm="api"'

    # --------------------------------------------------------------------------
    # Internal helpers — extracted for testability
    # --------------------------------------------------------------------------

    def _extract_token(self, request) -> str | None:
        """Return the raw JWT string from the Authorization header, or None."""
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith(_BEARER_PREFIX):
            return None
        token = auth_header[len(_BEARER_PREFIX):].strip()
        return token if token else None

    def _decode_jwt(self, token: str) -> dict:
        """
        Decode ``token`` by delegating to the shared ``_decode_token`` decoder.

        Raises
        ------
        jose.JWTError
            If the token is invalid, expired, or validation fails (including
            network errors when fetching JWKS).
        """
        payload = _decode_token(token)
        if payload is None:
            raise JWTError("Token validation failed.")
        return payload

    def _fetch_user_from_db(self, uid: str) -> dict | None:
        """Fetch user row from the database directly (no REST API required)."""
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(
                "SELECT id, role, email FROM public.customers WHERE id = %s LIMIT 1",
                [uid],
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {"id": str(row[0]), "role": row[1], "email": row[2]}
