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
from django.conf import settings
from jose import ExpiredSignatureError, JWTError, jwt
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

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

    def __init__(self, *, uid: str, role: str) -> None:
        self.id = uid
        self.role = role

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

        user = SupabaseUser(uid=uid, role=db_user.get("role", "authenticated"))
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
        return auth_header[len(_BEARER_PREFIX):]

    def _decode_jwt(self, token: str) -> dict:
        """
        Fetch the Supabase JWKS and decode ``token`` using python-jose.

        Raises
        ------
        requests.RequestException
            On any network error while fetching the JWKS endpoint.
        jose.ExpiredSignatureError
            If the token's ``exp`` claim is in the past.
        jose.JWTError
            For any other JWT validation failure (bad signature, algorithm, …).
        """
        supabase_url = getattr(settings, "SUPABASE_URL", "")
        jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"

        resp = requests.get(jwks_url, timeout=10)
        resp.raise_for_status()
        jwks = resp.json()

        # python-jose accepts the raw JWKS dict directly
        payload = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},  # Supabase aud varies by project
        )
        return payload

    def _fetch_user_from_db(self, uid: str) -> dict | None:
        """
        Fetch the user row from the Supabase REST API (``users`` table).

        Returns the first matching row as a dict, or ``None`` if not found.

        Raises
        ------
        requests.RequestException
            On network failure (caller may choose to surface as 503).
        """
        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_key = getattr(settings, "SUPABASE_ANON_KEY", "")

        rest_url = f"{supabase_url}/rest/v1/users"
        resp = requests.get(
            rest_url,
            params={"id": f"eq.{uid}", "select": "id,role,email"},
            headers={
                "apikey": supabase_key,
                "Authorization": f"Bearer {supabase_key}",
            },
            timeout=10,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            return None
        return rows[0]
