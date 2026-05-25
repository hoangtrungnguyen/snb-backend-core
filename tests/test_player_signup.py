"""
Tests for POST /auth/player/signup endpoint.

Mocks the Supabase HTTP call — no real network requests are made.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


class PlayerSignupViewTests(TestCase):
    """Tests for the POST /auth/player/signup endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/signup"

    def _mock_supabase_success(self):
        """Build a successful Supabase mock response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id": "user-uuid-456",
            "email": "player@example.com",
        }
        return mock_resp

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_signup_success_returns_201_and_user(self):
        """Valid credentials → 201 with message and user."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["message"], "Confirmation email sent")
        self.assertIn("user", body)
        self.assertEqual(body["user"]["id"], "user-uuid-456")
        self.assertEqual(body["user"]["email"], "player@example.com")

    def test_signup_calls_supabase_signup_endpoint(self):
        """View must call Supabase /auth/v1/signup with email + password."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        called_url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        self.assertIn("/auth/v1/signup", called_url)
        posted_json = call_kwargs[1].get("json") or (call_kwargs[0][1] if len(call_kwargs[0]) > 1 else {})
        self.assertEqual(posted_json.get("email"), "player@example.com")
        self.assertEqual(posted_json.get("password"), "pass1234")

    # ------------------------------------------------------------------
    # Password validation — 400 before calling Supabase
    # ------------------------------------------------------------------

    def test_password_too_short_returns_400(self):
        """Password < 8 chars → 400 validation_error without hitting Supabase."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "ab1"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "validation_error")
        self.assertIn("detail", body)
        mock_post.assert_not_called()

    def test_password_no_letter_returns_400(self):
        """Password with no letter → 400 validation_error without hitting Supabase."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "12345678"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "validation_error")
        self.assertIn("detail", body)
        mock_post.assert_not_called()

    def test_password_no_digit_returns_400(self):
        """Password with no digit → 400 validation_error without hitting Supabase."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "abcdefgh"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "validation_error")
        self.assertIn("detail", body)
        mock_post.assert_not_called()

    def test_password_exactly_8_chars_with_letter_and_digit_passes_validation(self):
        """Password of exactly 8 chars with letter + digit → passes validation."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "abcdef1!"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 201)

    # ------------------------------------------------------------------
    # Email already registered — Supabase returns 422
    # ------------------------------------------------------------------

    def test_email_already_registered_returns_409(self):
        """Supabase 422 (existing email) → endpoint returns 409."""
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {
            "code": 422,
            "msg": "User already registered",
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "existing@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["error"], "email_already_registered")

    # ------------------------------------------------------------------
    # Missing fields — 400 before hitting Supabase
    # ------------------------------------------------------------------

    def test_missing_email_returns_400(self):
        """Request without email → 400."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_missing_password_returns_400(self):
        """Request without password → 400."""
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

    def test_wrong_http_method_returns_405(self):
        """GET request → 405 Method Not Allowed."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    # ------------------------------------------------------------------
    # Supabase service unavailable
    # ------------------------------------------------------------------

    def test_supabase_unavailable_returns_502(self):
        """Network error to Supabase → 502."""
        import requests as req_lib

        with patch("auth_ext.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 502)
