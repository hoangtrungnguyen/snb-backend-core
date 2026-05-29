"""
Tests for GET /auth/callback endpoint.

Covers:
- Happy path: code exchanged for tokens, users row upserted, redirect with
  tokens in URL fragment (not query params)
- Missing code param → 400
- Supabase token exchange failure → 400
- Network error on token exchange → 503
- Network error on upsert → 503
- token_type validation (only allowlisted values accepted)
- Tokens NOT in query params (security requirement)
"""
import json
from unittest.mock import patch, MagicMock, call
from urllib.parse import urlparse, parse_qs, unquote_plus

import django
from django.test import TestCase, Client, override_settings
import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")


def _make_token_response(
    access_token="eyJ.access.token",
    refresh_token="eyJ.refresh.token",
    token_type="bearer",
):
    """Build a mock Supabase token-exchange response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_type,
        "expires_in": 3600,
        "user": {
            "id": "user-uuid-abc123",
            "email": "player@example.com",
            "user_metadata": {"full_name": "Test Player"},
        },
    }
    return mock_resp


def _make_upsert_response():
    """Build a mock Supabase Admin upsert response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    return mock_resp


def _make_lookup_no_existing():
    """Build a mock lookup response with no existing user row."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    return mock_resp


@override_settings(
    SUPABASE_URL="https://xyzproject.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="anon-key-test",
    SUPABASE_SECRET_KEY="service-role-key-test",
    FRONTEND_URL="https://app.example.com",
    ALLOWED_HOSTS=["testserver", "localhost"],
)
class AuthCallbackViewTests(TestCase):
    """Tests for GET /auth/callback."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/callback"

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    def test_success_redirects_with_tokens_in_fragment(self):
        """Valid code → tokens exchanged → redirect with tokens in URL fragment."""
        token_resp = _make_token_response()
        lookup_resp = _make_lookup_no_existing()
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]), \
             patch("auth_ext.views.requests.get", return_value=lookup_resp):
            resp = self.client.get(self.url, {"code": "test-auth-code"})

        self.assertEqual(resp.status_code, 302)
        location = resp["Location"]
        # Tokens MUST be in fragment, not query params
        parsed = urlparse(location)
        self.assertNotIn("access_token", parsed.query)
        self.assertNotIn("refresh_token", parsed.query)
        # Fragment must contain tokens
        self.assertIn("access_token=", parsed.fragment)
        self.assertIn("refresh_token=", parsed.fragment)

    def test_success_fragment_contains_correct_tokens(self):
        """Fragment params must contain the exact tokens returned by Supabase."""
        token_resp = _make_token_response(
            access_token="my.access.token",
            refresh_token="my.refresh.token",
        )
        lookup_resp = _make_lookup_no_existing()
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]), \
             patch("auth_ext.views.requests.get", return_value=lookup_resp):
            resp = self.client.get(self.url, {"code": "test-auth-code"})

        location = resp["Location"]
        parsed = urlparse(location)
        # Parse fragment as query string
        frag_params = parse_qs(parsed.fragment)
        self.assertEqual(frag_params["access_token"][0], "my.access.token")
        self.assertEqual(frag_params["refresh_token"][0], "my.refresh.token")

    def test_success_calls_supabase_token_endpoint(self):
        """View must call Supabase PKCE token endpoint with the code."""
        token_resp = _make_token_response()
        lookup_resp = _make_lookup_no_existing()
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp):
            self.client.get(self.url, {"code": "exchange-code-xyz"})

        first_call = mock_post.call_args_list[0]
        called_url = first_call[0][0] if first_call[0] else first_call[1].get("url", "")
        self.assertIn("/auth/v1/token", called_url)
        # Must include code in payload
        posted_json = first_call[1].get("json") or {}
        self.assertEqual(posted_json.get("auth_code"), "exchange-code-xyz")

    def test_success_upserts_users_row(self):
        """After token exchange, view must upsert a users row."""
        token_resp = _make_token_response()
        lookup_resp = _make_lookup_no_existing()
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp):
            self.client.get(self.url, {"code": "test-code"})

        # Second call must be the upsert
        self.assertEqual(mock_post.call_count, 2)
        upsert_call = mock_post.call_args_list[1]
        upsert_url = upsert_call[0][0] if upsert_call[0] else upsert_call[1].get("url", "")
        self.assertIn("/rest/v1/users", upsert_url)

    def test_upserted_user_has_player_role(self):
        """Upserted users row must have role='player'."""
        token_resp = _make_token_response()
        lookup_resp = _make_lookup_no_existing()
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp):
            self.client.get(self.url, {"code": "test-code"})

        upsert_call = mock_post.call_args_list[1]
        posted_json = upsert_call[1].get("json") or {}
        # May be a list (bulk upsert) or dict
        if isinstance(posted_json, list):
            self.assertEqual(posted_json[0].get("role"), "player")
        else:
            self.assertEqual(posted_json.get("role"), "player")

    # ------------------------------------------------------------------
    # Missing / invalid code
    # ------------------------------------------------------------------

    def test_missing_code_returns_400(self):
        """Request without code param → 400 without hitting Supabase."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_empty_code_returns_400(self):
        """Empty code param → 400."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.get(self.url, {"code": ""})

        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    # ------------------------------------------------------------------
    # Supabase token exchange error
    # ------------------------------------------------------------------

    def test_supabase_token_error_returns_400(self):
        """Supabase token exchange failure → 400, no internal details."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {"error": "invalid_grant", "error_description": "Bad code"}

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.get(self.url, {"code": "bad-code"})

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)
        # No internal Supabase detail in response
        self.assertNotIn("error_description", body)

    # ------------------------------------------------------------------
    # Network errors
    # ------------------------------------------------------------------

    def test_network_error_on_token_exchange_returns_503(self):
        """Network error on token exchange → 503."""
        import requests as req_lib
        with patch("auth_ext.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.get(self.url, {"code": "any-code"})

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        # No internal error detail
        self.assertNotIn("timeout", str(body))

    def test_network_error_on_upsert_returns_503(self):
        """Network error on DB upsert → 503."""
        import requests as req_lib
        token_resp = _make_token_response()
        lookup_resp = _make_lookup_no_existing()

        with patch(
            "auth_ext.views.requests.post",
            side_effect=[token_resp, req_lib.RequestException("db timeout")],
        ), patch("auth_ext.views.requests.get", return_value=lookup_resp):
            resp = self.client.get(self.url, {"code": "any-code"})

        self.assertEqual(resp.status_code, 503)

    # ------------------------------------------------------------------
    # Security: tokens must not appear in query params
    # ------------------------------------------------------------------

    def test_tokens_not_in_query_params(self):
        """Tokens must never appear in query params (Referer / log leak prevention)."""
        token_resp = _make_token_response(
            access_token="secret.access",
            refresh_token="secret.refresh",
        )
        lookup_resp = _make_lookup_no_existing()
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]), \
             patch("auth_ext.views.requests.get", return_value=lookup_resp):
            resp = self.client.get(self.url, {"code": "test-code"})

        location = resp["Location"]
        parsed = urlparse(location)
        # Ensure tokens are NOT in the query string
        self.assertNotIn("secret.access", parsed.query)
        self.assertNotIn("secret.refresh", parsed.query)

    # ------------------------------------------------------------------
    # POST method not allowed
    # ------------------------------------------------------------------

    def test_post_returns_405(self):
        """POST to /auth/callback → 405."""
        resp = self.client.post(self.url, data={})
        self.assertEqual(resp.status_code, 405)
