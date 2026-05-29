"""
JWT Authentication Middleware for SportBuddies.

Reads the ``Authorization: Bearer <token>`` header, verifies the JWT
against the Supabase JWKS endpoint, extracts the user's role from
``app_metadata.role``, and attaches a lightweight user object to
``request.user``.

On any failure (missing/invalid/expired token, network error fetching
JWKS) the middleware sets ``request.user`` to ``AnonymousUser`` and
lets the request continue — views are responsible for enforcing auth.

Settings required
-----------------
SUPABASE_JWKS_URL : str
    The JWKS endpoint URL, e.g.
    ``https://<project>.supabase.co/auth/v1/.well-known/jwks.json``

Optional
--------
SUPABASE_JWT_AUDIENCE : str  (default: "authenticated")
    The expected ``aud`` claim value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import requests
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from jose import JWTError, jwt as jose_jwt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User types
# ---------------------------------------------------------------------------


@dataclass
class AuthenticatedUser:
    """Minimal user object attached to ``request.user`` on success."""

    id: str
    email: str
    role: str

    #: Always False — mirrors Django's AnonymousUser.is_anonymous contract
    is_anonymous: bool = False
    #: Always True — mirrors Django's AbstractBaseUser contract
    is_authenticated: bool = True


# ---------------------------------------------------------------------------
# JWKS helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _fetch_jwks(jwks_url: str) -> dict:
    """
    Fetch and cache the JWKS from *jwks_url*.

    The result is cached indefinitely for the lifetime of the process.
    In production, restart the process or call ``_fetch_jwks.cache_clear()``
    to pick up rotated keys.

    Raises :exc:`requests.RequestException` on network errors.
    """
    response = requests.get(jwks_url, timeout=5)
    response.raise_for_status()
    return response.json()


def _get_jwks_url() -> str:
    return getattr(settings, "SUPABASE_JWKS_URL", "")


def _get_audience() -> str:
    return getattr(settings, "SUPABASE_JWT_AUDIENCE", "authenticated")


# ---------------------------------------------------------------------------
# Token decoding
# ---------------------------------------------------------------------------


def _decode_token(token: str) -> dict | None:
    """
    Verify *token* against the Supabase JWKS and return the decoded claims.

    Returns ``None`` on any error (bad signature, expired, network failure …).
    """
    jwks_url = _get_jwks_url()
    if not jwks_url:
        logger.warning(
            "auth_ext: SUPABASE_JWKS_URL is not configured — "
            "all JWT verifications will fail."
        )
        return None

    try:
        jwks = _fetch_jwks(jwks_url)
    except Exception:
        logger.exception("auth_ext: Failed to fetch JWKS from %s", jwks_url)
        return None

    try:
        claims = jose_jwt.decode(
            token,
            jwks,
            # Supabase's new asymmetric JWT signing keys use ES256; legacy/other
            # projects may use RS256. Accept both so verification works across
            # the new and old key models.
            algorithms=["ES256", "RS256"],
            audience=_get_audience(),
        )
        return claims
    except JWTError as exc:
        # Clear the JWKS cache so rotated keys are re-fetched on the next request.
        _fetch_jwks.cache_clear()
        logger.debug("auth_ext: JWT validation failed: %s", exc)
        return None
    except (ValueError, KeyError) as exc:
        # Malformed base64 or missing JWKS fields — log at DEBUG to avoid
        # leaking token bytes into logs.
        logger.debug("auth_ext: Malformed token or JWKS structure: %s", type(exc).__name__)
        return None
    except Exception:
        logger.exception("auth_ext: Unexpected error while decoding JWT")
        return None


def _extract_role(claims: dict) -> str:
    """Return the role from ``app_metadata.role``, defaulting to ``'user'``."""
    app_metadata = claims.get("app_metadata") or {}
    return app_metadata.get("role") or "user"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class JWTAuthMiddleware:
    """
    Django middleware that populates ``request.user`` from a Supabase JWT.

    Place **after** ``django.contrib.auth.middleware.AuthenticationMiddleware``
    in ``MIDDLEWARE`` so it can override the session-based user when a valid
    Bearer token is present.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only attempt JWT auth when a Bearer token is actually present.
        # This preserves session-authenticated users set by Django's
        # AuthenticationMiddleware when no Authorization header is sent.
        if JWTAuthMiddleware._extract_token(request) is not None:
            request.user = self._authenticate(request)
        return self.get_response(request)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_token(request) -> str | None:
        """Return the raw JWT string from the Authorization header, or None."""
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer "):].strip()
        return token if token else None

    @staticmethod
    def _authenticate(request):
        """Return an ``AuthenticatedUser`` or ``AnonymousUser``."""
        token = JWTAuthMiddleware._extract_token(request)
        if token is None:
            return AnonymousUser()

        claims = _decode_token(token)
        if claims is None:
            return AnonymousUser()

        return AuthenticatedUser(
            id=claims.get("sub", ""),
            email=claims.get("email", ""),
            role=_extract_role(claims),
        )
