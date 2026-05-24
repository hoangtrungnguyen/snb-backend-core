"""
Tests for SupabaseJWTAuthentication DRF authentication class.

Tests are organized by behaviour:
- No token present → return None (allow other auth backends to proceed)
- Valid token → return (SupabaseUser, token) tuple
- Invalid / expired token → raise AuthenticationFailed (HTTP 401)
- JWKS network error → raise AuthenticationFailed (HTTP 503-friendly message)
"""
import os
import time
from unittest.mock import patch, MagicMock

import pytest

# Make sure Django is configured before importing DRF / auth_ext
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")

from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from auth_ext.authentication import SupabaseJWTAuthentication, SupabaseUser


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

FAKE_UID = "11111111-aaaa-bbbb-cccc-000000000001"
FAKE_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.fake.sig"

# A minimal decoded payload that mimics a Supabase JWT
FAKE_PAYLOAD = {
    "sub": FAKE_UID,
    "email": "player@example.com",
    "role": "authenticated",
    "exp": int(time.time()) + 3600,
    "iat": int(time.time()),
    "aud": "authenticated",
}

# A minimal Supabase user row from the DB
FAKE_DB_USER = {
    "id": FAKE_UID,
    "role": "player",
    "email": "player@example.com",
}


def _make_request(token=None):
    """Return a DRF request with an optional Authorization header."""
    factory = APIRequestFactory()
    req = factory.get("/fake/")
    if token:
        req.META["HTTP_AUTHORIZATION"] = f"Bearer {token}"
    return req


# ---------------------------------------------------------------------------
# SupabaseUser unit tests
# ---------------------------------------------------------------------------

class TestSupabaseUser:
    def test_is_authenticated_is_true(self):
        user = SupabaseUser(uid=FAKE_UID, role="player")
        assert user.is_authenticated is True

    def test_id_attribute(self):
        user = SupabaseUser(uid=FAKE_UID, role="player")
        assert user.id == FAKE_UID

    def test_role_attribute(self):
        user = SupabaseUser(uid=FAKE_UID, role="admin")
        assert user.role == "admin"

    def test_str_representation(self):
        user = SupabaseUser(uid=FAKE_UID, role="player")
        assert FAKE_UID in str(user)


# ---------------------------------------------------------------------------
# authenticate() — no token
# ---------------------------------------------------------------------------

class TestNoToken:
    def test_no_auth_header_returns_none(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=None)
        result = auth.authenticate(req)
        assert result is None

    def test_non_bearer_scheme_returns_none(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=None)
        req.META["HTTP_AUTHORIZATION"] = "Basic dXNlcjpwYXNz"
        result = auth.authenticate(req)
        assert result is None


# ---------------------------------------------------------------------------
# authenticate() — valid token
# ---------------------------------------------------------------------------

class TestValidToken:
    def test_returns_user_and_token_tuple(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", return_value=FAKE_PAYLOAD), \
             patch.object(auth, "_fetch_user_from_db", return_value=FAKE_DB_USER):
            result = auth.authenticate(req)

        assert result is not None
        user, token = result
        assert token == FAKE_TOKEN

    def test_user_id_equals_sub_claim(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", return_value=FAKE_PAYLOAD), \
             patch.object(auth, "_fetch_user_from_db", return_value=FAKE_DB_USER):
            user, _ = auth.authenticate(req)

        assert user.id == FAKE_UID

    def test_user_role_from_db(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", return_value=FAKE_PAYLOAD), \
             patch.object(auth, "_fetch_user_from_db", return_value=FAKE_DB_USER):
            user, _ = auth.authenticate(req)

        assert user.role == "player"  # from DB, not JWT

    def test_user_is_authenticated(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", return_value=FAKE_PAYLOAD), \
             patch.object(auth, "_fetch_user_from_db", return_value=FAKE_DB_USER):
            user, _ = auth.authenticate(req)

        assert user.is_authenticated is True

    def test_fetch_user_called_with_sub(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", return_value=FAKE_PAYLOAD), \
             patch.object(auth, "_fetch_user_from_db", return_value=FAKE_DB_USER) as mock_fetch:
            auth.authenticate(req)

        mock_fetch.assert_called_once_with(FAKE_UID)


# ---------------------------------------------------------------------------
# authenticate() — invalid / expired token
# ---------------------------------------------------------------------------

class TestInvalidToken:
    def test_expired_token_raises_authentication_failed(self):
        from jose import ExpiredSignatureError

        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", side_effect=ExpiredSignatureError("expired")):
            with pytest.raises(AuthenticationFailed):
                auth.authenticate(req)

    def test_invalid_signature_raises_authentication_failed(self):
        from jose import JWTError

        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", side_effect=JWTError("bad sig")):
            with pytest.raises(AuthenticationFailed):
                auth.authenticate(req)

    def test_missing_sub_claim_raises_authentication_failed(self):
        payload_without_sub = {k: v for k, v in FAKE_PAYLOAD.items() if k != "sub"}

        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", return_value=payload_without_sub):
            with pytest.raises(AuthenticationFailed):
                auth.authenticate(req)

    def test_user_not_in_db_raises_authentication_failed(self):
        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", return_value=FAKE_PAYLOAD), \
             patch.object(auth, "_fetch_user_from_db", return_value=None):
            with pytest.raises(AuthenticationFailed):
                auth.authenticate(req)


# ---------------------------------------------------------------------------
# authenticate() — JWKS network error
# ---------------------------------------------------------------------------

class TestNetworkError:
    def test_jwks_network_error_raises_authentication_failed(self):
        import requests as req_lib

        auth = SupabaseJWTAuthentication()
        req = _make_request(token=FAKE_TOKEN)

        with patch.object(auth, "_decode_jwt", side_effect=req_lib.RequestException("timeout")):
            with pytest.raises(AuthenticationFailed) as exc_info:
                auth.authenticate(req)

        # Should mention the upstream service in the error message
        assert "service" in str(exc_info.value).lower() or "unavailable" in str(exc_info.value).lower() or "network" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# _decode_jwt() unit tests (JWKS fetching logic)
# ---------------------------------------------------------------------------

class TestDecodeJwt:
    """Test the internal JWKS-based decode method in isolation."""

    def test_decode_jwt_fetches_jwks_and_decodes(self):
        """_decode_jwt must fetch JWKS from Supabase and verify the token."""
        # Build a fake JWKS response
        fake_jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "test-key-1",
                    "n": "test-n",
                    "e": "AQAB",
                    "alg": "RS256",
                    "use": "sig",
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = fake_jwks

        auth = SupabaseJWTAuthentication()

        with patch("auth_ext.authentication.requests.get", return_value=mock_resp) as mock_get, \
             patch("auth_ext.authentication.jwt.decode", return_value=FAKE_PAYLOAD) as mock_decode:
            result = auth._decode_jwt(FAKE_TOKEN)

        assert result == FAKE_PAYLOAD
        mock_get.assert_called_once()
        # JWKS URL must point to Supabase
        called_url = mock_get.call_args[0][0]
        assert "supabase" in called_url.lower() or "jwks" in called_url.lower()

    def test_decode_jwt_raises_on_network_error(self):
        import requests as req_lib

        auth = SupabaseJWTAuthentication()

        with patch("auth_ext.authentication.requests.get", side_effect=req_lib.RequestException("timeout")):
            with pytest.raises(req_lib.RequestException):
                auth._decode_jwt(FAKE_TOKEN)


# ---------------------------------------------------------------------------
# _fetch_user_from_db() unit tests
# ---------------------------------------------------------------------------

class TestFetchUserFromDb:
    """Test the internal DB-fetch method in isolation."""

    def test_returns_none_when_user_not_found(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = []  # empty list = not found

        auth = SupabaseJWTAuthentication()

        with patch("auth_ext.authentication.requests.get", return_value=mock_resp):
            result = auth._fetch_user_from_db(FAKE_UID)

        assert result is None

    def test_returns_user_dict_when_found(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = [FAKE_DB_USER]

        auth = SupabaseJWTAuthentication()

        with patch("auth_ext.authentication.requests.get", return_value=mock_resp):
            result = auth._fetch_user_from_db(FAKE_UID)

        assert result == FAKE_DB_USER
        assert result["id"] == FAKE_UID
