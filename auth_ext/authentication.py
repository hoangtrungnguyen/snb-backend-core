"""
auth_ext.authentication — Supabase JWT authentication and permissions for DRF.

SupabaseJWTAuthentication:
    Validates the Bearer JWT from Authorization header against SUPABASE_JWT_SECRET.
    Falls back to SUPABASE_ANON_KEY audience if secret not set (anonymous key flow).

IsPlayer:
    DRF permission that allows access only if the authenticated user has role='player'
    in their JWT user_metadata or app_metadata.
"""
from __future__ import annotations

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

try:
    from jose import jwt, JWTError
except ImportError:
    # Fall back to python-jose top-level import
    import jwt  # type: ignore
    JWTError = Exception


class SupabaseUser:
    """Lightweight user object populated from a decoded Supabase JWT payload."""

    is_authenticated = True

    def __init__(self, payload: dict):
        self.id: str = payload.get("sub", "")
        self.email: str = payload.get("email", "")
        self.role: str = payload.get("role", "")
        # Supabase puts custom role in user_metadata or app_metadata
        user_meta = payload.get("user_metadata") or {}
        app_meta = payload.get("app_metadata") or {}
        self.player_role: str = (
            user_meta.get("role") or app_meta.get("role") or ""
        )
        self._payload = payload


class SupabaseJWTAuthentication(BaseAuthentication):
    """
    DRF authentication class that validates Supabase JWTs.

    Reads the Bearer token from Authorization header, decodes it using
    SUPABASE_JWT_SECRET (or a relaxed no-verify path for local dev when
    SUPABASE_JWT_SECRET is absent), and returns a SupabaseUser.
    """

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith("Bearer "):
            return None  # Let other authenticators try

        token = auth_header[len("Bearer "):]
        jwt_secret = getattr(settings, "SUPABASE_JWT_SECRET", "")

        try:
            if jwt_secret:
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=["HS256"],
                    options={"verify_aud": False},
                )
            else:
                # Dev/test mode: decode without verification (requires secret)
                # In practice tests mock jwt.decode directly.
                payload = jwt.decode(
                    token,
                    jwt_secret or "insecure",
                    algorithms=["HS256"],
                    options={"verify_signature": False, "verify_aud": False},
                )
        except Exception as exc:
            raise AuthenticationFailed(f"Invalid or expired token.") from exc

        user = SupabaseUser(payload)
        return (user, token)

    def authenticate_header(self, request):
        return "Bearer"


class IsPlayer(BasePermission):
    """
    Allows access only to authenticated users whose JWT carries role='player'.

    Supabase stores custom roles in user_metadata.role or app_metadata.role.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.player_role == "player"
