"""
Tests for POST /auth/owner/signup endpoint.

Mocks the supabase-py admin client — no real network requests are made.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from supabase_auth.errors import AuthApiError

from auth_ext.supabase_client import get_admin_client


def _user_response(user_id="owner-uuid-123", email="owner@example.com"):
    return MagicMock(user=MagicMock(id=user_id, email=email))


def _build_admin_mock(create_user_return=None, create_user_side_effect=None,
                      update_side_effect=None):
    client = MagicMock()
    if create_user_side_effect is not None:
        client.auth.admin.create_user.side_effect = create_user_side_effect
    else:
        client.auth.admin.create_user.return_value = (
            create_user_return or _user_response()
        )

    update_chain = client.table.return_value.update.return_value.eq.return_value
    if update_side_effect is not None:
        update_chain.execute.side_effect = update_side_effect
    else:
        update_chain.execute.return_value = MagicMock()
    return client


class OwnerSignupViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = "/auth/owner/signup"
        get_admin_client.cache_clear()

    def test_signup_success_returns_201_and_user(self):
        admin = _build_admin_mock()
        with patch("auth_ext.views.get_admin_client", return_value=admin):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["message"], "Owner account created")
        self.assertEqual(body["user"]["id"], "owner-uuid-123")
        self.assertEqual(body["user"]["email"], "owner@example.com")

    def test_signup_calls_admin_create_user_with_email_confirm(self):
        admin = _build_admin_mock()
        with patch("auth_ext.views.get_admin_client", return_value=admin):
            self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        admin.auth.admin.create_user.assert_called_once_with(
            {"email": "owner@example.com", "password": "pass1234", "email_confirm": True}
        )

    def test_signup_promotes_customers_role_to_owner(self):
        admin = _build_admin_mock()
        with patch("auth_ext.views.get_admin_client", return_value=admin):
            self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        admin.table.assert_called_with("customers")
        admin.table.return_value.update.assert_called_with({"role": "owner"})
        admin.table.return_value.update.return_value.eq.assert_called_with(
            "id", "owner-uuid-123"
        )

    def test_password_too_short_returns_400(self):
        with patch("auth_ext.views.get_admin_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "ab1"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "validation_error")
        mock_factory.assert_not_called()

    def test_password_no_letter_returns_400(self):
        with patch("auth_ext.views.get_admin_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "12345678"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_factory.assert_not_called()

    def test_password_no_digit_returns_400(self):
        with patch("auth_ext.views.get_admin_client") as mock_factory:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "abcdefgh"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 400)
        mock_factory.assert_not_called()

    def test_email_already_registered_returns_409(self):
        admin = _build_admin_mock(
            create_user_side_effect=AuthApiError("User already registered", 422, None)
        )
        with patch("auth_ext.views.get_admin_client", return_value=admin):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "existing@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"], "email_already_registered")

    def test_missing_email_returns_400(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({"password": "pass1234"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_password_returns_400(self):
        resp = self.client.post(
            self.url,
            data=json.dumps({"email": "owner@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_json_body_returns_400(self):
        resp = self.client.post(self.url, data="not-json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_wrong_http_method_returns_405(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_supabase_unavailable_returns_502(self):
        admin = _build_admin_mock(
            create_user_side_effect=RuntimeError("connection refused")
        )
        with patch("auth_ext.views.get_admin_client", return_value=admin):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 502)

    def test_customers_update_failure_returns_503(self):
        admin = _build_admin_mock(
            update_side_effect=RuntimeError("postgrest down"),
        )
        with patch("auth_ext.views.get_admin_client", return_value=admin):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 503)

    def test_admin_create_non_422_error_returns_503(self):
        admin = _build_admin_mock(
            create_user_side_effect=AuthApiError("bad request", 400, None)
        )
        with patch("auth_ext.views.get_admin_client", return_value=admin):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "pass1234"}),
                content_type="application/json",
            )
        self.assertEqual(resp.status_code, 503)
