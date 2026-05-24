"""
DRF authentication backend for Supabase JWTs.

Integrates with :mod:`auth_ext.middleware` — reuses the same JWKS-backed
``_decode_token`` helper so key-rotation and caching behaviour is identical.

DRF integration contract
------------------------
``authenticate(request)`` must return one of:
    - ``None``           — no credentials present; DRF moves to the next authenticator
    - ``(user, token)``  — credentials valid; DRF sets ``request.user`` and ``request.auth``
    - raise ``AuthenticationFailed`` — credentials present but invalid

Settings required
-----------------
SUPABASE_JWKS_URL : str
    The JWKS endpoint URL.

Optional
--------
SUPABASE_JWT_AUDIENCE : str  (default: "authenticated")
"""

from __future__ import annotations

import logging

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from auth_ext.middleware import (
    AuthenticatedUser,
    _decode_token,
    _extract_role,
)

logger = logging.getLogger(__name__)


class SupabaseJWTAuthentication(BaseAuthentication):
    """
    DRF ``BaseAuthentication`` subclass that verifies Supabase JWTs.

    Reads the ``Authorization: Bearer <token>`` header.  On success it
    returns an ``(AuthenticatedUser, raw_token)`` tuple.  On absence of
    a Bearer token it returns ``None`` so that downstream authenticators
    (e.g. session) are tried.  On a present-but-invalid token it raises
    ``AuthenticationFailed`` (HTTP 401).
    """

    def authenticate(self, request) -> tuple[AuthenticatedUser, str] | None:
        """
        Authenticate the incoming request.

        Returns
        -------
        None
            No ``Authorization: Bearer`` header — let DRF try the next class.
        (AuthenticatedUser, str)
            Valid token — ``request.user`` and ``request.auth`` are set by DRF.

        Raises
        ------
        AuthenticationFailed
            Token is present but invalid, expired, or cannot be verified.
        """
        raw_token = self._extract_token(request)
        if raw_token is None:
            return None  # No credentials — pass to next authenticator

        claims = _decode_token(raw_token)
        if claims is None:
            raise AuthenticationFailed("Invalid or expired token.")

        user = AuthenticatedUser(
            id=claims.get("sub", ""),
            email=claims.get("email", ""),
            role=_extract_role(claims),
        )
        return (user, raw_token)

    def authenticate_header(self, request) -> str:
        """
        Return a ``WWW-Authenticate`` header value for 401 responses.

        DRF uses this to distinguish 401 (unauthenticated) from 403
        (authenticated but forbidden).  Returning a non-empty string
        causes DRF to emit 401 when authentication fails.
        """
        return 'Bearer realm="api"'

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_token(request) -> str | None:
        """Return the raw JWT string from the Authorization header, or None."""
        # Support both DRF's wrapped request and plain Django HttpRequest
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer "):].strip()
        return token if token else None
