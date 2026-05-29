"""
Tests for POST /auth/player/signup — Google OAuth conflict edge case.

Edge case: player attempts email/password signup with an email that is already
linked to a Google OAuth account. The endpoint must return HTTP 409 with body
{"code": "account_exists_other_provider"} so the client can show a merge prompt.

Flow:
  1. supabase-py auth.sign_up raises AuthApiError(status=422).
  2. View calls Supabase Admin API (still via `requests.get`) to inspect
     identity providers for that email.
  3. If the user has a provider == "google" identity, the view returns:
       HTTP 409  {"code": "account_exists_other_provider"}
  4. If the user only has an "email" identity, the existing behaviour applies:
       HTTP 409  {"error": "email_already_registered"}

All Supabase calls are mocked — no real HTTP requests are made.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client, override_settings
from supabase_auth.errors import AuthApiError

from auth_ext.supabase_client import get_anon_client


_TEST_SETTINGS = dict(
    SUPABASE_URL="https://test.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="test-anon-key",
    SUPABASE_SECRET_KEY="test-service-role-key",
)


def _build_anon_mock_raising_422():
    client = MagicMock()
    client.auth.sign_up.side_effect = AuthApiError("User already registered", 422, None)
    return client


def _admin_user_google(email="player@example.com"):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "users": [
            {
                "id": "user-uuid-999",
                "email": email,
                "identities": [{"provider": "google", "identity_data": {"email": email}}],
            }
        ]
    }
    return mock_resp


def _admin_user_email_only(email="player@example.com"):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "users": [
            {
                "id": "user-uuid-888",
                "email": email,
                "identities": [{"provider": "email", "identity_data": {"email": email}}],
            }
        ]
    }
    return mock_resp


class PlayerSignupGoogleOAuthConflictTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/signup"
        get_anon_client.cache_clear()

    @override_settings(**_TEST_SETTINGS)
    def test_google_oauth_conflict_returns_409_account_exists_other_provider(self):
        with patch("auth_ext.views.get_anon_client", return_value=_build_anon_mock_raising_422()), \
             patch("auth_ext.views.requests.get", return_value=_admin_user_google()):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 409)
        body = resp.json()
        self.assertEqual(body["code"], "account_exists_other_provider")
        self.assertNotIn("error_description", body)
        self.assertNotIn("msg", body)

    @override_settings(**_TEST_SETTINGS)
    def test_google_oauth_conflict_does_not_expose_password_or_email_existence(self):
        with patch("auth_ext.views.get_anon_client", return_value=_build_anon_mock_raising_422()), \
             patch("auth_ext.views.requests.get", return_value=_admin_user_google()):
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

    @override_settings(**_TEST_SETTINGS)
    def test_email_only_conflict_still_returns_email_already_registered(self):
        with patch("auth_ext.views.get_anon_client", return_value=_build_anon_mock_raising_422()), \
             patch("auth_ext.views.requests.get", return_value=_admin_user_email_only()):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "existing@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "email_already_registered")

    @override_settings(**_TEST_SETTINGS)
    def test_admin_api_called_with_email_after_422(self):
        with patch("auth_ext.views.get_anon_client", return_value=_build_anon_mock_raising_422()), \
             patch("auth_ext.views.requests.get", return_value=_admin_user_google()) as mock_get:
            self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        called_url = args[0] if args else kwargs.get("url", "")
        self.assertIn("/auth/v1/admin/users", called_url)
        self.assertEqual((kwargs.get("params") or {}).get("email"), "player@example.com")

    @override_settings(**_TEST_SETTINGS)
    def test_admin_api_error_falls_back_to_email_already_registered(self):
        admin_err = MagicMock()
        admin_err.status_code = 500
        admin_err.json.return_value = {"message": "internal error"}
        with patch("auth_ext.views.get_anon_client", return_value=_build_anon_mock_raising_422()), \
             patch("auth_ext.views.requests.get", return_value=admin_err):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "email_already_registered")

    @override_settings(**_TEST_SETTINGS)
    def test_admin_api_network_error_falls_back_to_email_already_registered(self):
        import requests as req_lib
        with patch("auth_ext.views.get_anon_client", return_value=_build_anon_mock_raising_422()), \
             patch("auth_ext.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "email_already_registered")

    @override_settings(**_TEST_SETTINGS)
    def test_signup_transport_error_returns_502(self):
        client = MagicMock()
        client.auth.sign_up.side_effect = RuntimeError("conn refused")
        with patch("auth_ext.views.get_anon_client", return_value=client):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        self.assertNotIn("detail", body)
        self.assertNotIn("conn refused", str(body))
