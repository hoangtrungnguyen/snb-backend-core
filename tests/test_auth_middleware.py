"""
Tests for auth_ext.middleware.JWTAuthMiddleware

Covers:
- Missing Authorization header → request.user is NOT overwritten (session preserved)
- Malformed header (no Bearer) → request.user is NOT overwritten (session preserved)
- Invalid JWT (bad signature) → AnonymousUser
- Expired JWT → AnonymousUser
- Valid JWT → request.user is AuthenticatedUser with id, email, role
- Role extracted from app_metadata.role claim
- Role defaults to 'user' if claim missing
- JWKS fetch failure → AnonymousUser (do not raise)
- JWTError clears the JWKS cache (key rotation support)
- ValueError/KeyError during decode logged at DEBUG, not EXCEPTION
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from auth_ext.middleware import JWTAuthMiddleware, _fetch_jwks


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_response(request):
    """Dummy get_response callable for middleware."""
    return MagicMock()


def _make_request(auth_header=None):
    factory = RequestFactory()
    req = factory.get("/")
    if auth_header is not None:
        req.META["HTTP_AUTHORIZATION"] = auth_header
    return req


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures: RSA key pair + JWT helpers
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rsa_keypair():
    """Generate a fresh RSA key pair for signing test JWTs."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    return private_key


@pytest.fixture(scope="module")
def jwks_from_keypair(rsa_keypair):
    """
    Build a minimal JWKS dict from the test RSA private key.
    Returns (jwks_dict, kid).
    """
    import base64
    from cryptography.hazmat.primitives.asymmetric.rsa import (
        RSAPrivateKey,
    )

    pub = rsa_keypair.public_key()
    pub_nums = pub.public_numbers()

    def _b64url_uint(n: int) -> str:
        length = (n.bit_length() + 7) // 8
        b = n.to_bytes(length, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    kid = "test-key-1"
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url_uint(pub_nums.n),
        "e": _b64url_uint(pub_nums.e),
    }
    return {"keys": [jwk]}, kid


def _make_token(rsa_keypair, kid, payload_overrides=None):
    """Sign a JWT with the test private key."""
    from cryptography.hazmat.primitives import serialization
    from jose import jwt as jose_jwt

    pem = rsa_keypair.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    now = int(time.time())
    payload = {
        "sub": "user-uuid-123",
        "email": "test@example.com",
        "app_metadata": {"role": "admin"},
        "iat": now - 10,
        "exp": now + 3600,
        "aud": "authenticated",
    }
    if payload_overrides:
        payload.update(payload_overrides)

    return jose_jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────

class TestJWTAuthMiddlewareMissingOrMalformedHeader:
    """No / bad Authorization header → request.user is left untouched."""

    def test_no_auth_header_preserves_existing_user(self):
        """Without a Bearer token the middleware must not overwrite request.user."""
        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request()
        sentinel = MagicMock(name="session_user")
        request.user = sentinel
        middleware(request)
        assert request.user is sentinel

    def test_non_bearer_scheme_preserves_existing_user(self):
        """Basic-auth header is not a Bearer token — leave request.user alone."""
        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request("Basic dXNlcjpwYXNz")
        sentinel = MagicMock(name="session_user")
        request.user = sentinel
        middleware(request)
        assert request.user is sentinel

    def test_bearer_without_token_preserves_existing_user(self):
        """'Bearer ' (empty token) is not a valid Bearer header — leave user alone."""
        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request("Bearer ")
        sentinel = MagicMock(name="session_user")
        request.user = sentinel
        middleware(request)
        assert request.user is sentinel

    def test_bearer_invalid_token_sets_anonymous(self):
        """A Bearer header with an invalid JWT still results in AnonymousUser."""
        with patch("auth_ext.middleware._fetch_jwks", side_effect=Exception("fail")):
            middleware = JWTAuthMiddleware(_get_response)
            request = _make_request("Bearer garbage.token.here")
            middleware(request)
            assert request.user.is_anonymous


class TestJWTAuthMiddlewareInvalidTokens:
    """Bad / expired tokens → AnonymousUser (no exception propagated)."""

    @patch("auth_ext.middleware._fetch_jwks")
    def test_invalid_signature_sets_anonymous(self, mock_fetch, rsa_keypair, jwks_from_keypair):
        jwks, kid = jwks_from_keypair
        mock_fetch.return_value = jwks

        # Sign with a *different* key
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        other_key = rsa.generate_private_key(65537, 2048, default_backend())
        token = _make_token(other_key, kid)

        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request(f"Bearer {token}")
        middleware(request)
        assert request.user.is_anonymous

    @patch("auth_ext.middleware._fetch_jwks")
    def test_expired_token_sets_anonymous(self, mock_fetch, rsa_keypair, jwks_from_keypair):
        jwks, kid = jwks_from_keypair
        mock_fetch.return_value = jwks

        token = _make_token(rsa_keypair, kid, {"exp": int(time.time()) - 10, "iat": int(time.time()) - 100})

        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request(f"Bearer {token}")
        middleware(request)
        assert request.user.is_anonymous

    def test_jwks_fetch_failure_sets_anonymous(self):
        with patch("auth_ext.middleware._fetch_jwks", side_effect=Exception("network error")):
            middleware = JWTAuthMiddleware(_get_response)
            request = _make_request("Bearer some.fake.token")
            middleware(request)
            assert request.user.is_anonymous


class TestJWTAuthMiddlewareValidToken:
    """Valid JWT → authenticated user attached to request.user."""

    @patch("auth_ext.middleware._fetch_jwks")
    def test_valid_token_sets_authenticated_user(self, mock_fetch, rsa_keypair, jwks_from_keypair):
        jwks, kid = jwks_from_keypair
        mock_fetch.return_value = jwks

        token = _make_token(rsa_keypair, kid)

        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request(f"Bearer {token}")
        middleware(request)

        assert not request.user.is_anonymous
        assert request.user.id == "user-uuid-123"
        assert request.user.email == "test@example.com"
        assert request.user.role == "admin"

    @patch("auth_ext.middleware._fetch_jwks")
    def test_missing_role_defaults_to_user(self, mock_fetch, rsa_keypair, jwks_from_keypair):
        jwks, kid = jwks_from_keypair
        mock_fetch.return_value = jwks

        token = _make_token(rsa_keypair, kid, {"app_metadata": {}})

        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request(f"Bearer {token}")
        middleware(request)

        assert not request.user.is_anonymous
        assert request.user.role == "user"

    @patch("auth_ext.middleware._fetch_jwks")
    def test_no_app_metadata_defaults_role_to_user(self, mock_fetch, rsa_keypair, jwks_from_keypair):
        jwks, kid = jwks_from_keypair
        mock_fetch.return_value = jwks

        token = _make_token(rsa_keypair, kid, {"app_metadata": None})

        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request(f"Bearer {token}")
        middleware(request)

        assert not request.user.is_anonymous
        assert request.user.role == "user"

    @patch("auth_ext.middleware._fetch_jwks")
    def test_user_object_has_expected_attributes(self, mock_fetch, rsa_keypair, jwks_from_keypair):
        jwks, kid = jwks_from_keypair
        mock_fetch.return_value = jwks

        token = _make_token(rsa_keypair, kid)

        middleware = JWTAuthMiddleware(_get_response)
        request = _make_request(f"Bearer {token}")
        middleware(request)

        user = request.user
        assert hasattr(user, "id")
        assert hasattr(user, "email")
        assert hasattr(user, "role")
        assert hasattr(user, "is_anonymous")
        assert hasattr(user, "is_authenticated")
        assert user.is_authenticated is True


# ──────────────────────────────────────────────────────────────────────────────
# Tests for review-round fixes
# ──────────────────────────────────────────────────────────────────────────────

class TestJWTErrorClearsCacheOnKeyRotation:
    """Finding 1: JWTError must clear the lru_cache so rotated keys are re-fetched."""

    @patch("auth_ext.middleware._fetch_jwks")
    def test_jwt_error_clears_lru_cache(self, mock_fetch, rsa_keypair, jwks_from_keypair):
        """After a JWTError, _fetch_jwks.cache_clear() must have been called."""
        from auth_ext.middleware import _fetch_jwks as real_fetch_jwks

        # Sign token with a different key to force a JWTError on validation.
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.backends import default_backend
        other_key = rsa.generate_private_key(65537, 2048, default_backend())
        jwks, kid = jwks_from_keypair
        mock_fetch.return_value = jwks
        token = _make_token(other_key, kid)

        with patch.object(real_fetch_jwks, "cache_clear") as mock_clear:
            request = _make_request(f"Bearer {token}")
            middleware = JWTAuthMiddleware(_get_response)
            middleware(request)
            mock_clear.assert_called_once()

        assert request.user.is_anonymous


class TestValueKeyErrorLoggedAtDebug:
    """Finding 3: ValueError/KeyError during decode must be caught at DEBUG, not EXCEPTION."""

    @patch("auth_ext.middleware._fetch_jwks")
    def test_value_error_logged_at_debug_not_exception(self, mock_fetch, caplog):
        """ValueError from malformed token must not produce an EXCEPTION log entry."""
        import logging
        from jose import jwt as jose_jwt

        mock_fetch.return_value = {"keys": []}

        # Patch jose_jwt.decode to raise ValueError (malformed base64 etc.)
        with patch("auth_ext.middleware.jose_jwt.decode", side_effect=ValueError("bad base64")):
            with caplog.at_level(logging.DEBUG, logger="auth_ext.middleware"):
                request = _make_request("Bearer anything.goes.here")
                middleware = JWTAuthMiddleware(_get_response)
                middleware(request)

        assert request.user.is_anonymous
        # Must not emit ERROR or higher
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records, "ValueError must not produce ERROR/EXCEPTION log"

    @patch("auth_ext.middleware._fetch_jwks")
    def test_key_error_logged_at_debug_not_exception(self, mock_fetch, caplog):
        """KeyError from missing JWKS field must not produce an EXCEPTION log entry."""
        import logging

        mock_fetch.return_value = {"keys": []}

        with patch("auth_ext.middleware.jose_jwt.decode", side_effect=KeyError("kid")):
            with caplog.at_level(logging.DEBUG, logger="auth_ext.middleware"):
                request = _make_request("Bearer anything.goes.here")
                middleware = JWTAuthMiddleware(_get_response)
                middleware(request)

        assert request.user.is_anonymous
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records, "KeyError must not produce ERROR/EXCEPTION log"
