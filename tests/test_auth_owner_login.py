"""
Tests for POST /auth/owner/login endpoint.

Mocks the Supabase HTTP call — no real network requests are made.
"""
import json
from unittest.mock import patch, MagicMock

import django
from django.test import TestCase, Client

# Ensure Django is set up for tests
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")


class OwnerLoginViewTests(TestCase):
    """Tests for the POST /auth/owner/login endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/owner/login"

    def _mock_supabase_success(self):
        """Build a successful Supabase mock response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "eyJ.test.access",
            "refresh_token": "eyJ.test.refresh",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {
                "id": "user-uuid-123",
                "email": "owner@example.com",
                "role": "authenticated",
            },
        }
        return mock_resp

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_login_success_returns_tokens_and_user(self):
        """Valid credentials → 200 with access_token, refresh_token, user."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("access_token", body)
        self.assertIn("refresh_token", body)
        self.assertIn("user", body)
        self.assertEqual(body["access_token"], "eyJ.test.access")
        self.assertEqual(body["refresh_token"], "eyJ.test.refresh")
        self.assertEqual(body["user"]["email"], "owner@example.com")

    def test_login_calls_supabase_with_correct_params(self):
        """View must call Supabase auth REST API with email + password."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "secret"}),
                content_type="application/json",
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        # URL must contain the token endpoint
        called_url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        self.assertIn("/auth/v1/token", called_url)
        # Payload must contain email and password
        posted_json = call_kwargs[1].get("json") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
        self.assertEqual(posted_json.get("email"), "owner@example.com")
        self.assertEqual(posted_json.get("password"), "secret")

    # ------------------------------------------------------------------
    # Invalid credentials — Supabase returns 4xx
    # ------------------------------------------------------------------

    def test_invalid_credentials_returns_401(self):
        """Wrong password → Supabase 400 → endpoint returns 401."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Invalid login credentials",
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "wrong"}),
                content_type="application/json",
            )

        self.assertIn(resp.status_code, [400, 401])
        body = resp.json()
        self.assertIn("error", body)

    # ------------------------------------------------------------------
    # Bad request — missing fields
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
                data=json.dumps({"email": "owner@example.com"}),
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

    def test_wrong_http_method_returns_405(self):
        """GET request → 405 Method Not Allowed."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
