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
                "email_confirmed_at": "2024-01-01T00:00:00Z",
            },
        }
        return mock_resp

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_login_success_returns_tokens_and_user(self):
        """Valid credentials → 200 with access_token, refresh_token, user."""
        mock_resp = self._mock_supabase_success()
        mock_role_resp = MagicMock()
        mock_role_resp.status_code = 200
        mock_role_resp.json.return_value = [{"role": "owner"}]

        with patch("auth_ext.views.requests.post", return_value=mock_resp), \
             patch("auth_ext.views.requests.get", return_value=mock_role_resp):
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

    def test_wrong_password_returns_generic_401(self):
        """Wrong password → Supabase 400 → 401 with exact generic error body (no field discrimination)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Invalid login credentials",
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "wrongpassword"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body.get("error"), "invalid_credentials")
        self.assertEqual(body.get("detail"), "Invalid credentials")

    def test_unknown_email_returns_generic_401(self):
        """Unknown email → Supabase 400 → same 401 generic body (no user-enumeration leak)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Email not confirmed",
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "nobody@example.com", "password": "anything"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body.get("error"), "invalid_credentials")
        self.assertEqual(body.get("detail"), "Invalid credentials")

    def test_wrong_password_and_unknown_email_return_identical_body(self):
        """Wrong password and unknown email must return byte-for-byte identical response bodies."""
        wrong_password_mock = MagicMock()
        wrong_password_mock.status_code = 400
        wrong_password_mock.json.return_value = {
            "error": "invalid_grant",
            "error_description": "Invalid login credentials",
        }

        unknown_email_mock = MagicMock()
        unknown_email_mock.status_code = 400
        unknown_email_mock.json.return_value = {
            "error": "invalid_grant",
            "error_description": "User not found",
        }

        with patch("auth_ext.views.requests.post", return_value=wrong_password_mock):
            resp_wrong_pass = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "wrongpassword"}),
                content_type="application/json",
            )

        with patch("auth_ext.views.requests.post", return_value=unknown_email_mock):
            resp_unknown_email = self.client.post(
                self.url,
                data=json.dumps({"email": "nobody@example.com", "password": "anything"}),
                content_type="application/json",
            )

        self.assertEqual(resp_wrong_pass.status_code, 401)
        self.assertEqual(resp_unknown_email.status_code, 401)
        self.assertEqual(resp_wrong_pass.json(), resp_unknown_email.json(),
                         "Wrong password and unknown email must return identical response bodies")

    def test_supabase_401_returns_generic_401(self):
        """Supabase 401 response → endpoint returns generic 401 (no field discrimination)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.json.return_value = {
            "message": "JWT expired",
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body.get("error"), "invalid_credentials")
        self.assertEqual(body.get("detail"), "Invalid credentials")

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
