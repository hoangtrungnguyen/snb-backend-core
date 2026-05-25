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

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_login_success_returns_tokens_and_user(self):
        """Valid credentials + confirmed email → 200 with access_token, refresh_token, user."""
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
        self.assertEqual(body["access_token"], "eyJ.player.access")
        self.assertEqual(body["refresh_token"], "eyJ.player.refresh")
        self.assertEqual(body["user"]["email"], "player@example.com")

    def test_login_calls_supabase_with_correct_params(self):
        """View must call Supabase auth REST API with grant_type=password."""
        with patch("auth_ext.views.requests.post", return_value=_make_supabase_success()) as mock_post:
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
        mock_resp = _make_supabase_success(email_confirmed=True)
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
        with patch("auth_ext.views.requests.post", return_value=_make_supabase_success(email_confirmed=False)):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"], "email_not_verified")

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
        self.assertEqual(resp.json()["error"], "email_not_verified")

    # ------------------------------------------------------------------
    # Anti-enumeration: wrong password and unknown email → identical 401
    # ------------------------------------------------------------------

    def test_wrong_password_returns_401_with_generic_body(self):
        """Wrong password → Supabase 400 → 401 {"error":"invalid_credentials","detail":"Invalid credentials"}."""
        mock_resp = _make_supabase_4xx(400, "invalid_grant", "Invalid login credentials")

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "wrong_password"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_credentials")
        self.assertEqual(body["detail"], "Invalid credentials")

    def test_unknown_email_returns_401_with_generic_body(self):
        """Unknown email → Supabase 400 → same 401 {"error":"invalid_credentials","detail":"Invalid credentials"}."""
        # Supabase typically returns the same 400 for both unknown email and wrong password
        mock_resp = _make_supabase_4xx(400, "invalid_grant", "Invalid login credentials")

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "nonexistent@example.com", "password": "some_password"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertEqual(body["error"], "invalid_credentials")
        self.assertEqual(body["detail"], "Invalid credentials")

    def test_wrong_password_and_unknown_email_return_identical_bodies(self):
        """
        Anti-enumeration test: wrong password and unknown email must return
        exactly the same response body — no field discrimination.
        """
        # Supabase 400 for wrong password
        mock_wrong_pw = _make_supabase_4xx(400, "invalid_grant", "Invalid login credentials")
        # Supabase 400 for unknown email (same error code from Supabase)
        mock_unknown_email = _make_supabase_4xx(400, "invalid_grant", "Invalid login credentials")

        with patch("auth_ext.views.requests.post", return_value=mock_wrong_pw):
            resp_wrong_pw = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "wrong"}),
                content_type="application/json",
            )

        with patch("auth_ext.views.requests.post", return_value=mock_unknown_email):
            resp_unknown = self.client.post(
                self.url,
                data=json.dumps({"email": "nobody@example.com", "password": "anything"}),
                content_type="application/json",
            )

        self.assertEqual(resp_wrong_pw.status_code, resp_unknown.status_code)
        self.assertEqual(resp_wrong_pw.json(), resp_unknown.json(),
                         "Response bodies differ — enumeration is possible!")

    def test_all_4xx_supabase_responses_return_same_generic_401(self):
        """All Supabase 4xx responses (400–499) → same 401 generic response."""
        expected_status = 401
        expected_body = {"error": "invalid_credentials", "detail": "Invalid credentials"}

        for supabase_status in (400, 401, 403, 422, 429):
            with self.subTest(supabase_status=supabase_status):
                mock_resp = _make_supabase_4xx(supabase_status)

                with patch("auth_ext.views.requests.post", return_value=mock_resp):
                    resp = self.client.post(
                        self.url,
                        data=json.dumps({"email": "player@example.com", "password": "wrong"}),
                        content_type="application/json",
                    )

                self.assertEqual(resp.status_code, expected_status,
                                 f"Supabase {supabase_status} → expected 401, got {resp.status_code}")
                self.assertEqual(resp.json(), expected_body,
                                 f"Supabase {supabase_status} → body mismatch")

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
    # Upstream failure — network errors → 503
    # ------------------------------------------------------------------

    def test_supabase_network_error_returns_503(self):
        """Network failure calling Supabase → 503 Service Unavailable (not 401, not 502)."""
        with patch("auth_ext.views.requests.post",
                   side_effect=requests_module.RequestException("connection timeout")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 503)

    def test_supabase_network_error_does_not_return_401(self):
        """Network failures must NOT return 401 — they must be distinguishable from bad credentials."""
        with patch("auth_ext.views.requests.post",
                   side_effect=requests_module.RequestException("DNS resolution failed")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "secret"}),
                content_type="application/json",
            )
        self.assertNotEqual(resp.status_code, 401)
        self.assertEqual(resp.status_code, 503)
