"""
Tests for POST /auth/player/signup endpoint.

Mocks supabase-py auth client — no real network requests are made.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from supabase_auth.errors import AuthApiError

from auth_ext.supabase_client import get_anon_client


def _user_resp(user_id="user-uuid-456", email="player@example.com"):
    return MagicMock(user=MagicMock(id=user_id, email=email))


def _anon_mock(sign_up_return=None, sign_up_side_effect=None):
    client = MagicMock()
    if sign_up_side_effect is not None:
        client.auth.sign_up.side_effect = sign_up_side_effect
    else:
        client.auth.sign_up.return_value = sign_up_return or _user_resp()
    return client


class PlayerSignupViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/signup"
        get_anon_client.cache_clear()

    def test_signup_success_returns_201_and_user(self):
        with patch("auth_ext.views.get_anon_client", return_value=_anon_mock()):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["message"], "Confirmation email sent")
        self.assertEqual(body["user"]["id"], "user-uuid-456")
        self.assertEqual(body["user"]["email"], "player@example.com")

    def test_signup_calls_supabase_sign_up_with_email_and_password(self):
        client = _anon_mock()
        with patch("auth_ext.views.get_anon_client", return_value=client):
            self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        client.auth.sign_up.assert_called_once_with(
            {"email": "player@example.com", "password": "pass1234"}
        )

    def test_password_too_short_returns_400(self):
        with patch("auth_ext.views.get_anon_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "ab1"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "validation_error")
        mock_factory.assert_not_called()

    def test_password_no_letter_returns_400(self):
        with patch("auth_ext.views.get_anon_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "12345678"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_factory.assert_not_called()

    def test_password_no_digit_returns_400(self):
        with patch("auth_ext.views.get_anon_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "abcdefgh"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_factory.assert_not_called()

    def test_password_exactly_8_chars_with_letter_and_digit_passes_validation(self):
        with patch("auth_ext.views.get_anon_client", return_value=_anon_mock()):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "abcdef1!"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 201)

    def test_email_already_registered_returns_409(self):
        # 422 from auth + admin lookup with email-only provider → 409 email_already_registered
        anon = _anon_mock(sign_up_side_effect=AuthApiError("registered", 422, None))
        admin_resp = MagicMock()
        admin_resp.status_code = 200
        admin_resp.json.return_value = {
            "users": [{"identities": [{"provider": "email"}]}]
        }
        with patch("auth_ext.views.get_anon_client", return_value=anon), \
             patch("auth_ext.views.requests.get", return_value=admin_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "existing@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "email_already_registered")

    def test_missing_email_returns_400(self):
        with patch("auth_ext.views.get_anon_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_factory.assert_not_called()

    def test_missing_password_returns_400(self):
        with patch("auth_ext.views.get_anon_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_factory.assert_not_called()

    def test_non_json_body_returns_400(self):
        resp = self.client.post(self.url, data="not-json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_wrong_http_method_returns_405(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_supabase_unavailable_returns_502(self):
        anon = _anon_mock(sign_up_side_effect=RuntimeError("timeout"))
        with patch("auth_ext.views.get_anon_client", return_value=anon):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 502)
