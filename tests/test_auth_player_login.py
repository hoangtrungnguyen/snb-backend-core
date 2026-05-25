"""
Tests for POST /auth/player/login endpoint.

Anti-enumeration property: wrong password and unknown email must both return
the same 401 with identical response bodies — no field discrimination.

Mocks the Supabase HTTP call — no real network requests are made.
"""
import json
from unittest.mock import patch, MagicMock

import requests as requests_module
from django.test import TestCase, Client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_supabase_4xx(status_code: int, error: str = "invalid_grant",
                       error_description: str = "Invalid login credentials"):
    """Return a mock Supabase response with the given 4xx status."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = {"error": error, "error_description": error_description}
    return mock_resp


def _make_supabase_success(email_confirmed=True):
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


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class PlayerLoginViewTests(TestCase):
    """Tests for the POST /auth/player/login endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/login"

    def test_login_success_returns_tokens_and_user(self):
        with patch("auth_ext.views.requests.post", return_value=_make_supabase_success()):
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

    def test_unverified_email_returns_403(self):
        with patch("auth_ext.views.requests.post", return_value=_make_supabase_success(email_confirmed=False)):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "email_not_verified")

    def test_wrong_password_returns_401_with_generic_body(self):
        with patch("auth_ext.views.requests.post", return_value=_make_supabase_4xx(400)):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "wrong"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_credentials")
        self.assertEqual(body["detail"], "Invalid credentials")

    def test_all_4xx_supabase_responses_return_same_generic_401(self):
        expected_body = {"error": "invalid_credentials", "detail": "Invalid credentials"}
        for supabase_status in (400, 401, 403, 422, 429):
            with self.subTest(supabase_status=supabase_status):
                with patch("auth_ext.views.requests.post", return_value=_make_supabase_4xx(supabase_status)):
                    resp = self.client.post(
                        self.url,
                        data=json.dumps({"email": "player@example.com", "password": "wrong"}),
                        content_type="application/json",
                    )
                self.assertEqual(resp.status_code, 401)
                self.assertEqual(resp.json(), expected_body)

    def test_missing_email_returns_400(self):
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"password": "secret"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_missing_password_returns_400(self):
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_non_json_body_returns_400(self):
        resp = self.client.post(self.url, data="not-json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_wrong_http_method_returns_405(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_supabase_network_error_returns_503(self):
        with patch("auth_ext.views.requests.post",
                   side_effect=requests_module.RequestException("timeout")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 503)
