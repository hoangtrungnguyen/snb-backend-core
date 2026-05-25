"""
Tests for POST /auth/player/login endpoint.

Mocks the Supabase HTTP call — no real network requests are made.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


def _make_supabase_success_response(email_confirmed=True):
    """Build a successful Supabase mock response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    user = {
        "id": "player-uuid-456",
        "email": "player@example.com",
        "role": "authenticated",
    }
    if email_confirmed:
        user["email_confirmed_at"] = "2026-01-01T00:00:00Z"
    else:
        user["email_confirmed_at"] = None

    mock_resp.json.return_value = {
        "access_token": "eyJ.player.access",
        "refresh_token": "eyJ.player.refresh",
        "token_type": "bearer",
        "expires_in": 3600,
        "user": user,
    }
    return mock_resp


class PlayerLoginViewTests(TestCase):
    """Tests for the POST /auth/player/login endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/login"

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_login_success_returns_tokens_and_user(self):
        """Valid credentials + confirmed email → 200 with access_token, refresh_token, user."""
        mock_resp = _make_supabase_success_response(email_confirmed=True)

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertIn("user", body)
        self.assertEqual(body["access_token"], "eyJ.player.access")
        self.assertEqual(body["refresh_token"], "eyJ.player.refresh")
        self.assertEqual(body["user"]["email"], "player@example.com")

    def test_login_calls_supabase_with_correct_params(self):
        """View must call Supabase auth REST API with grant_type=password."""
        mock_resp = _make_supabase_success_response(email_confirmed=True)

        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        called_url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        self.assertIn("/auth/v1/token", called_url)
        posted_json = call_kwargs[1].get("json") or {}
        self.assertEqual(posted_json.get("email"), "player@example.com")
        self.assertEqual(posted_json.get("password"), "secret")

    def test_any_role_is_accepted(self):
        """Player login does not restrict by role — any authenticated user succeeds."""
        mock_resp = _make_supabase_success_response(email_confirmed=True)
        # Override role to something non-owner
        data = mock_resp.json.return_value
        data["user"]["role"] = "player"
        mock_resp.json.return_value = data

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # Email verification enforcement
    # ------------------------------------------------------------------

    def test_unverified_email_returns_403(self):
        """Supabase returns user with email_confirmed_at=null → 403 email_not_verified."""
        mock_resp = _make_supabase_success_response(email_confirmed=False)

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error"], "email_not_verified")

    def test_missing_email_confirmed_at_field_returns_403(self):
        """Supabase user dict without email_confirmed_at key → 403."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "eyJ.player.access",
            "refresh_token": "eyJ.player.refresh",
            "user": {
                "id": "player-uuid-456",
                "email": "player@example.com",
                # email_confirmed_at deliberately absent
            },
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body["error"], "email_not_verified")

    # ------------------------------------------------------------------
    # Invalid credentials — generic 401 (no enumeration)
    # ------------------------------------------------------------------

    def test_invalid_credentials_returns_401_with_generic_error(self):
        """Wrong password → Supabase 400 → endpoint returns 401 with 'invalid_credentials'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Invalid login credentials",
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "wrong"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_credentials")

    def test_supabase_401_returns_401_generic(self):
        """Supabase 401 → endpoint returns 401 with 'invalid_credentials'."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": "invalid_grant"}

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "wrong"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_credentials")

    # ------------------------------------------------------------------
    # Bad request — missing / malformed body
    # ------------------------------------------------------------------

    def test_missing_email_returns_400(self):
        """Request without email → 400 without hitting Supabase."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"password": "secret"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_missing_password_returns_400(self):
        """Request without password → 400 without hitting Supabase."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_non_json_body_returns_400(self):
        """Non-JSON body → 400."""
        resp = self.client.post(
            self.url,
            data="not-json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_body_returns_400(self):
        """Empty body → 400."""
        resp = self.client.post(
            self.url,
            data="",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # HTTP method
    # ------------------------------------------------------------------

    def test_wrong_http_method_returns_405(self):
        """GET request → 405 Method Not Allowed."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    # ------------------------------------------------------------------
    # Upstream failure
    # ------------------------------------------------------------------

    def test_supabase_network_error_returns_502(self):
        """Network failure calling Supabase → 502."""
        import requests as req_module
        with patch("auth_ext.views.requests.post", side_effect=req_module.RequestException("timeout")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 502)
