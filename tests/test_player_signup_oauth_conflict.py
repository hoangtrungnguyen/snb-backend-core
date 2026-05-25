"""
Tests for POST /auth/player/signup — Google OAuth conflict edge case.

Edge case: player attempts email/password signup with an email that is already
linked to a Google OAuth account. The endpoint must return HTTP 409 with body
{"code": "account_exists_other_provider"} so the client can show a merge prompt.

Supabase behaviour:
  1. POST /auth/v1/signup returns 422 (email already registered).
  2. View calls Supabase Admin API GET /auth/v1/admin/users?email=... to inspect
     identity providers.
  3. If the user has a provider == "google" identity, the view returns:
       HTTP 409  {"code": "account_exists_other_provider"}
  4. If the user only has an "email" identity, the existing behaviour applies:
       HTTP 409  {"error": "email_already_registered"}

All Supabase network calls are mocked — no real HTTP requests are made.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings


_TEST_SETTINGS = dict(
    SUPABASE_URL="https://test.supabase.co",
    SUPABASE_ANON_KEY="test-anon-key",
    SUPABASE_SERVICE_ROLE_KEY="test-service-role-key",
)


class PlayerSignupGoogleOAuthConflictTests(TestCase):
    """Tests for the Google OAuth conflict case at POST /auth/player/signup."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/signup"

    def _mock_supabase_422(self, msg="User already registered"):
        """Build a Supabase 422 response (email already registered)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"code": 422, "msg": msg}
        return mock_resp

    def _mock_admin_user_google(self, email="player@example.com"):
        """Build a Supabase Admin API response where user has google provider."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "users": [
                {
                    "id": "user-uuid-999",
                    "email": email,
                    "identities": [
                        {
                            "provider": "google",
                            "identity_data": {"email": email},
                        }
                    ],
                }
            ]
        }
        return mock_resp

    def _mock_admin_user_email_only(self, email="player@example.com"):
        """Build a Supabase Admin API response where user has only email provider."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "users": [
                {
                    "id": "user-uuid-888",
                    "email": email,
                    "identities": [
                        {
                            "provider": "email",
                            "identity_data": {"email": email},
                        }
                    ],
                }
            ]
        }
        return mock_resp

    # ------------------------------------------------------------------
    # Core AC: Google OAuth conflict → 409 account_exists_other_provider
    # ------------------------------------------------------------------

    @override_settings(**_TEST_SETTINGS)
    def test_google_oauth_conflict_returns_409_account_exists_other_provider(self):
        """
        Supabase 422 + admin lookup shows google provider
        → HTTP 409 {"code": "account_exists_other_provider"}.
        """
        signup_resp = self._mock_supabase_422()
        admin_resp = self._mock_admin_user_google()

        with patch("auth_ext.views.requests.post", return_value=signup_resp), \
             patch("auth_ext.views.requests.get", return_value=admin_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["code"], "account_exists_other_provider")
        # Must NOT leak internal error details
        self.assertNotIn("error_description", body)
        self.assertNotIn("msg", body)

    @override_settings(**_TEST_SETTINGS)
    def test_google_oauth_conflict_does_not_expose_password_or_email_existence(self):
        """
        Response body for oauth conflict must only contain 'code'
        — no email, no provider name, no internal Supabase message.
        """
        signup_resp = self._mock_supabase_422()
        admin_resp = self._mock_admin_user_google()

        with patch("auth_ext.views.requests.post", return_value=signup_resp), \
             patch("auth_ext.views.requests.get", return_value=admin_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        body = resp.json()
        self.assertIn("code", body)
        self.assertNotIn("email", body)
        self.assertNotIn("provider", body)
        self.assertNotIn("google", str(body))

    # ------------------------------------------------------------------
    # Email-only registration still returns 409 email_already_registered
    # ------------------------------------------------------------------

    @override_settings(**_TEST_SETTINGS)
    def test_email_only_conflict_still_returns_email_already_registered(self):
        """
        Supabase 422 + admin lookup shows only email provider
        → original HTTP 409 {"error": "email_already_registered"} unchanged.
        """
        signup_resp = self._mock_supabase_422()
        admin_resp = self._mock_admin_user_email_only()

        with patch("auth_ext.views.requests.post", return_value=signup_resp), \
             patch("auth_ext.views.requests.get", return_value=admin_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "existing@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["error"], "email_already_registered")

    # ------------------------------------------------------------------
    # Admin API called with correct parameters
    # ------------------------------------------------------------------

    @override_settings(**_TEST_SETTINGS)
    def test_admin_api_called_with_email_after_422(self):
        """
        After a 422 from signup, view calls Supabase Admin API with correct
        endpoint and email query parameter.
        """
        signup_resp = self._mock_supabase_422()
        admin_resp = self._mock_admin_user_google()

        with patch("auth_ext.views.requests.post", return_value=signup_resp), \
             patch("auth_ext.views.requests.get", return_value=admin_resp) as mock_get:
            self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        mock_get.assert_called_once()
        call_args = mock_get.call_args
        called_url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
        self.assertIn("/auth/v1/admin/users", called_url)
        params = call_args[1].get("params") or {}
        self.assertEqual(params.get("email"), "player@example.com")

    # ------------------------------------------------------------------
    # Admin API error → fall back to email_already_registered (safe default)
    # ------------------------------------------------------------------

    @override_settings(**_TEST_SETTINGS)
    def test_admin_api_error_falls_back_to_email_already_registered(self):
        """
        If admin API call fails (non-200), fall back safely to 409
        {"error": "email_already_registered"} without leaking provider details.
        """
        signup_resp = self._mock_supabase_422()
        admin_err_resp = MagicMock()
        admin_err_resp.status_code = 500
        admin_err_resp.json.return_value = {"message": "internal error"}

        with patch("auth_ext.views.requests.post", return_value=signup_resp), \
             patch("auth_ext.views.requests.get", return_value=admin_err_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["error"], "email_already_registered")

    @override_settings(**_TEST_SETTINGS)
    def test_admin_api_network_error_falls_back_to_email_already_registered(self):
        """
        If admin API call raises a network exception, fall back safely to 409
        {"error": "email_already_registered"} — do not propagate to 503.
        """
        import requests as req_lib

        signup_resp = self._mock_supabase_422()

        with patch("auth_ext.views.requests.post", return_value=signup_resp), \
             patch("auth_ext.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["error"], "email_already_registered")

    # ------------------------------------------------------------------
    # Signup network error → 503 (per task patterns)
    # ------------------------------------------------------------------

    @override_settings(**_TEST_SETTINGS)
    def test_signup_network_error_returns_503(self):
        """
        Network error reaching Supabase signup endpoint → 503 Service Unavailable.
        No internal detail is exposed in the body.
        """
        import requests as req_lib

        with patch("auth_ext.views.requests.post", side_effect=req_lib.RequestException("conn refused")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        # No internal error details
        self.assertNotIn("detail", body)
        self.assertNotIn("conn refused", str(body))
