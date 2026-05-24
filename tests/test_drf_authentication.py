"""
Tests for auth_ext.authentication.SupabaseJWTAuthentication (DRF).

Covers:
- Returns None when no Authorization header is present (DRF skips to next authenticator)
- Returns None when Authorization header is not "Bearer ..." form
- Raises AuthenticationFailed when token is present but invalid/expired
- Returns (AuthenticatedUser, token) tuple on success
- Settings wiring: REST_FRAMEWORK uses SupabaseJWTAuthentication as default
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed

from auth_ext.authentication import SupabaseJWTAuthentication
from auth_ext.middleware import AuthenticatedUser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(auth_header: str | None = None):
    """Return a DRF-wrapped request with optional Authorization header."""
    factory = RequestFactory()
    request = factory.get("/")
    if auth_header is not None:
        request.META["HTTP_AUTHORIZATION"] = auth_header

    # Wrap in DRF Request so .META is accessible (plain Django request also works)
    return request


VALID_CLAIMS = {
    "sub": "user-uuid-123",
    "email": "test@example.com",
    "app_metadata": {"role": "player"},
    "aud": "authenticated",
}


# ---------------------------------------------------------------------------
# SupabaseJWTAuthentication tests
# ---------------------------------------------------------------------------


class TestSupabaseJWTAuthentication:
    """Unit tests for SupabaseJWTAuthentication."""

    def setup_method(self):
        self.authenticator = SupabaseJWTAuthentication()

    # ------------------------------------------------------------------
    # No token → returns None (anonymous pass-through)
    # ------------------------------------------------------------------

    def test_no_auth_header_returns_none(self):
        request = _make_request()
        result = self.authenticator.authenticate(request)
        assert result is None

    def test_non_bearer_auth_header_returns_none(self):
        request = _make_request("Basic dXNlcjpwYXNz")
        result = self.authenticator.authenticate(request)
        assert result is None

    def test_bearer_without_token_returns_none(self):
        """'Bearer ' with trailing whitespace only → treat as missing."""
        request = _make_request("Bearer   ")
        result = self.authenticator.authenticate(request)
        assert result is None

    # ------------------------------------------------------------------
    # Invalid / expired token → raises AuthenticationFailed
    # ------------------------------------------------------------------

    @patch("auth_ext.authentication._decode_token", return_value=None)
    def test_invalid_token_raises_authentication_failed(self, mock_decode):
        request = _make_request("Bearer invalid.jwt.token")
        with pytest.raises(AuthenticationFailed):
            self.authenticator.authenticate(request)

    @patch("auth_ext.authentication._decode_token", return_value=None)
    def test_expired_token_raises_authentication_failed(self, mock_decode):
        request = _make_request("Bearer expired.jwt.here")
        with pytest.raises(AuthenticationFailed) as exc_info:
            self.authenticator.authenticate(request)
        assert "Invalid or expired" in str(exc_info.value.detail)

    # ------------------------------------------------------------------
    # Valid token → returns (user, token) tuple
    # ------------------------------------------------------------------

    @patch("auth_ext.authentication._decode_token", return_value=VALID_CLAIMS)
    def test_valid_token_returns_user_token_tuple(self, mock_decode):
        raw_token = "a.valid.jwt"
        request = _make_request(f"Bearer {raw_token}")
        result = self.authenticator.authenticate(request)

        assert result is not None
        user, token = result
        assert token == raw_token

    @patch("auth_ext.authentication._decode_token", return_value=VALID_CLAIMS)
    def test_valid_token_user_has_correct_fields(self, mock_decode):
        request = _make_request("Bearer a.valid.jwt")
        user, token = self.authenticator.authenticate(request)

        assert isinstance(user, AuthenticatedUser)
        assert user.id == "user-uuid-123"
        assert user.email == "test@example.com"
        assert user.role == "player"
        assert user.is_authenticated is True
        assert user.is_anonymous is False

    @patch("auth_ext.authentication._decode_token", return_value=VALID_CLAIMS)
    def test_valid_token_decode_called_with_raw_token(self, mock_decode):
        raw_token = "header.payload.sig"
        request = _make_request(f"Bearer {raw_token}")
        self.authenticator.authenticate(request)
        mock_decode.assert_called_once_with(raw_token)

    # ------------------------------------------------------------------
    # Role defaults
    # ------------------------------------------------------------------

    @patch(
        "auth_ext.authentication._decode_token",
        return_value={"sub": "u1", "email": "a@b.com"},
    )
    def test_missing_role_defaults_to_user(self, mock_decode):
        request = _make_request("Bearer some.jwt.token")
        user, _ = self.authenticator.authenticate(request)
        assert user.role == "user"


# ---------------------------------------------------------------------------
# Settings wiring check
# ---------------------------------------------------------------------------


class TestDRFSettingsWiring:
    """Verify REST_FRAMEWORK settings include SupabaseJWTAuthentication."""

    def test_default_authentication_classes_includes_supabase_jwt(self):
        """conftest configures minimal settings; we assert the full base.py path."""
        from django.conf import settings

        # The REST_FRAMEWORK setting may not be present in conftest-minimal settings,
        # so we import base.py content directly for this check.
        import importlib
        import sys

        # Attempt to read from already-configured settings first
        drf_settings = getattr(settings, "REST_FRAMEWORK", None)
        if drf_settings is not None:
            auth_classes = drf_settings.get("DEFAULT_AUTHENTICATION_CLASSES", [])
            assert "auth_ext.authentication.SupabaseJWTAuthentication" in auth_classes
        else:
            # Validate by reading the base.py module source
            import os

            base_settings_path = os.path.join(
                os.path.dirname(__file__), "..", "spb_core", "settings", "base.py"
            )
            with open(base_settings_path) as f:
                content = f.read()
            assert "SupabaseJWTAuthentication" in content
            assert "DEFAULT_AUTHENTICATION_CLASSES" in content
