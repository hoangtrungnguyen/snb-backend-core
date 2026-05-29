"""
Tests for POST /auth/refresh endpoint.

Mocks the Supabase HTTP call — no real network requests are made.

AC:
- POST /auth/refresh accepts {"refresh_token": "..."}
- Calls Supabase /auth/v1/token?grant_type=refresh_token with {"refresh_token": "..."}
- Returns {"access_token": "...", "refresh_token": "...", "user": {...}} on success
- Returns 401 {"error": "invalid_token"} on invalid/expired refresh token
- Returns 400 on missing/malformed request body
- Uses SUPABASE_PUBLISHABLE_KEY for the call
"""
import json
import requests as requests_lib
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


class TokenRefreshViewTests(TestCase):
    """Tests for the POST /auth/refresh endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/refresh"

    def _mock_supabase_success(self):
        """Build a successful Supabase refresh response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "eyJ.new.access",
            "refresh_token": "eyJ.new.refresh",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {
                "id": "user-uuid-456",
                "email": "owner@example.com",
                "role": "authenticated",
            },
        }
        return mock_resp

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_refresh_success_returns_tokens_and_user(self):
        """Valid refresh_token -> 200 with new access_token, refresh_token, user."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"refresh_token": "valid-refresh-token"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertIn("user", body)
        self.assertEqual(body["access_token"], "eyJ.new.access")
        self.assertEqual(body["refresh_token"], "eyJ.new.refresh")
        self.assertEqual(body["user"]["email"], "owner@example.com")

    def test_refresh_calls_supabase_with_correct_params(self):
        """View must call Supabase token endpoint with grant_type=refresh_token."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            self.client.post(
                self.url,
                data=json.dumps({"refresh_token": "valid-refresh-token"}),
                content_type="application/json",
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args

        # URL must contain the token endpoint
        called_url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        self.assertIn("/auth/v1/token", called_url)

        # params must include grant_type=refresh_token
        called_params = call_kwargs[1].get("params", {})
        self.assertEqual(called_params.get("grant_type"), "refresh_token")

        # JSON body must contain refresh_token
        posted_json = call_kwargs[1].get("json", {})
        self.assertEqual(posted_json.get("refresh_token"), "valid-refresh-token")

    def test_refresh_uses_supabase_anon_key_header(self):
        """View must send SUPABASE_PUBLISHABLE_KEY in the apikey header."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            with self.settings(SUPABASE_PUBLISHABLE_KEY="test-anon-key"):
                self.client.post(
                    self.url,
                    data=json.dumps({"refresh_token": "valid-refresh-token"}),
                    content_type="application/json",
                )

        call_headers = mock_post.call_args[1].get("headers", {})
        self.assertEqual(call_headers.get("apikey"), "test-anon-key")

    # ------------------------------------------------------------------
    # Invalid / expired refresh token — Supabase returns 4xx
    # ------------------------------------------------------------------

    def test_invalid_refresh_token_returns_401_with_invalid_token(self):
        """Expired/invalid refresh_token -> 401 {"error": "invalid_token"}."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Invalid Refresh Token: Already Used",
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"refresh_token": "expired-token"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_token")

    def test_supabase_401_returns_401_with_invalid_token(self):
        """Supabase 401 -> endpoint returns 401 {"error": "invalid_token"}."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {"error": "invalid_grant"}

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"refresh_token": "bad-token"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_token")

    def test_supabase_422_returns_401_with_invalid_token(self):
        """Supabase 422 -> endpoint returns 401 {"error": "invalid_token"}."""
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"error": "unprocessable"}

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"refresh_token": "bad-token"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_token")

    # ------------------------------------------------------------------
    # Bad request — missing/malformed body
    # ------------------------------------------------------------------

    def test_missing_refresh_token_returns_400(self):
        """Request without refresh_token field -> 400."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_non_json_body_returns_400(self):
        """Non-JSON body -> 400."""
        resp = self.client.post(
            self.url,
            data="not-json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_empty_body_returns_400(self):
        """Empty body -> 400."""
        resp = self.client.post(
            self.url,
            data="",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_wrong_http_method_returns_405(self):
        """GET request -> 405 Method Not Allowed."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    # ------------------------------------------------------------------
    # Network error — Supabase unreachable
    # ------------------------------------------------------------------

    def test_request_exception_returns_502_service_unavailable(self):
        """requests.RequestException -> 502 {"error": "service_unavailable"} with no detail field."""
        with patch(
            "auth_ext.views.requests.post",
            side_effect=requests_lib.RequestException("connection refused"),
        ):
            resp = self.client.post(
                self.url,
                data=json.dumps({"refresh_token": "any-token"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertEqual(body["error"], "service_unavailable")
        self.assertNotIn("detail", body)
