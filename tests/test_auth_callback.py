"""
Tests for GET /auth/callback endpoint.

Supabase redirects here after email verification.  This endpoint handles both:
- PKCE flow:      ?code=<code>
- Token-hash flow: ?token_hash=<hash>&type=email

No real network requests are made — Supabase HTTP calls are mocked.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings


def _supabase_token_response(access_token="acc.test", refresh_token="ref.test"):
    """Build a typical Supabase /auth/v1/token success response."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "user": {"id": "user-uuid-1", "email": "user@example.com"},
    }
    return mock


def _supabase_verify_response(access_token="acc.test", refresh_token="ref.test"):
    """Build a typical Supabase /auth/v1/verify success response."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": 3600,
        "user": {"id": "user-uuid-1", "email": "user@example.com"},
    }
    return mock


def _supabase_error_response(status=400):
    """Build a Supabase error response."""
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Token has expired or is invalid",
    }
    return mock


# ---------------------------------------------------------------------------
# PKCE code flow
# ---------------------------------------------------------------------------

class AuthCallbackPKCETests(TestCase):
    """Tests for ?code=<code> (PKCE) flow."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/callback"

    def test_pkce_success_returns_200_with_tokens(self):
        """Valid code → exchange with Supabase → 200 with tokens."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_token_response()):
            resp = self.client.get(self.url, {"code": "pkce-auth-code-123"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "verified")
        self.assertEqual(body["access_token"], "acc.test")
        self.assertEqual(body["refresh_token"], "ref.test")

    def test_pkce_calls_supabase_token_endpoint(self):
        """PKCE flow must POST to /auth/v1/token?grant_type=pkce."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_token_response()) as mock_post:
            self.client.get(self.url, {"code": "pkce-auth-code-123"})

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        self.assertIn("/auth/v1/token", call_url)
        call_params = mock_post.call_args[1].get("params", {})
        self.assertEqual(call_params.get("grant_type"), "pkce")

    def test_pkce_sends_code_in_body(self):
        """PKCE request body must include auth_code."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_token_response()) as mock_post:
            self.client.get(self.url, {"code": "my-pkce-code"})

        posted_json = mock_post.call_args[1].get("json", {})
        self.assertEqual(posted_json.get("auth_code"), "my-pkce-code")

    def test_pkce_failure_returns_400(self):
        """Supabase rejects code → 400 {"error": "verification_failed"}."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_error_response()):
            resp = self.client.get(self.url, {"code": "bad-code"})

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "verification_failed")

    @override_settings(FRONTEND_URL="https://app.sportbuddies.io")
    def test_pkce_redirects_to_frontend_when_configured(self):
        """If FRONTEND_URL is set, redirect there with tokens as URL fragment (not query params)."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_token_response()):
            resp = self.client.get(self.url, {"code": "pkce-code"})

        # Should be a redirect (3xx) to FRONTEND_URL
        self.assertIn(resp.status_code, [301, 302])
        location = resp.get("Location", "")
        self.assertIn("sportbuddies.io", location)
        # Tokens must be in the URL fragment (after #), not in query params
        self.assertIn("#", location)
        fragment_start = location.index("#")
        fragment = location[fragment_start:]
        self.assertIn("access_token=", fragment)
        self.assertIn("refresh_token=", fragment)
        # Ensure tokens are NOT in the query string part
        query_part = location[:fragment_start]
        self.assertNotIn("access_token=", query_part)
        self.assertNotIn("refresh_token=", query_part)


# ---------------------------------------------------------------------------
# token_hash flow
# ---------------------------------------------------------------------------

class AuthCallbackTokenHashTests(TestCase):
    """Tests for ?token_hash=<hash>&type=email flow."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/callback"

    def test_token_hash_success_returns_200_with_tokens(self):
        """Valid token_hash → verify with Supabase → 200 with tokens."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_verify_response()):
            resp = self.client.get(
                self.url,
                {"token_hash": "hash-abc-123", "type": "email"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "verified")
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)

    def test_token_hash_calls_supabase_verify_endpoint(self):
        """token_hash flow must POST to /auth/v1/verify."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_verify_response()) as mock_post:
            self.client.get(
                self.url,
                {"token_hash": "hash-abc-123", "type": "email"},
            )

        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        self.assertIn("/auth/v1/verify", call_url)

    def test_token_hash_sends_hash_and_type_in_body(self):
        """Verify request body must include token_hash and type."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_verify_response()) as mock_post:
            self.client.get(
                self.url,
                {"token_hash": "my-hash", "type": "email"},
            )

        posted_json = mock_post.call_args[1].get("json", {})
        self.assertEqual(posted_json.get("token_hash"), "my-hash")
        self.assertEqual(posted_json.get("type"), "email")

    def test_token_hash_failure_returns_400(self):
        """Supabase rejects hash → 400 {"error": "verification_failed"}."""
        with patch("auth_ext.views.requests.post", return_value=_supabase_error_response()):
            resp = self.client.get(
                self.url,
                {"token_hash": "expired-hash", "type": "email"},
            )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "verification_failed")

    def test_invalid_token_type_returns_400_without_calling_supabase(self):
        """Disallowed type value → 400 before any Supabase call."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.get(
                self.url,
                {"token_hash": "some-hash", "type": "malicious_type"},
            )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "verification_failed")
        # Supabase must NOT have been contacted
        mock_post.assert_not_called()

    def test_allowed_token_types_accepted(self):
        """Each type in the allowlist (signup, recovery, invite) is accepted."""
        for allowed_type in ("signup", "recovery", "invite"):
            with self.subTest(token_type=allowed_type):
                with patch(
                    "auth_ext.views.requests.post",
                    return_value=_supabase_verify_response(),
                ):
                    resp = self.client.get(
                        self.url,
                        {"token_hash": "hash-abc", "type": allowed_type},
                    )
                self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# Missing / invalid parameters
# ---------------------------------------------------------------------------

class AuthCallbackMissingParamsTests(TestCase):
    """Tests for requests with no recognisable parameters."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/callback"

    def test_no_params_returns_400(self):
        """Request with no query params → 400 verification_failed."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "verification_failed")

    def test_only_type_without_hash_returns_400(self):
        """type=email without token_hash → 400."""
        resp = self.client.get(self.url, {"type": "email"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "verification_failed")

    def test_network_error_returns_400(self):
        """RequestException from Supabase → 400 verification_failed."""
        import requests as req_lib
        with patch("auth_ext.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.get(self.url, {"code": "some-code"})

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "verification_failed")

    def test_callback_only_accepts_get(self):
        """POST to /auth/callback → 405 Method Not Allowed."""
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 405)
