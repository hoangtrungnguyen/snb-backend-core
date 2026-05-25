"""
Tests for email verification enforcement on POST /auth/owner/login endpoint.

After a successful Supabase signInWithPassword, the view checks user.email_confirmed_at.
If null or absent, returns 403 {"error": "email_not_verified"}.

Mocks the Supabase HTTP call — no real network requests are made.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


class OwnerLoginEmailVerificationTests(TestCase):
    """Tests for email verification enforcement in POST /auth/owner/login."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/owner/login"

    def _mock_supabase_success(self, email_confirmed=True):
        """Build a successful Supabase mock response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        user = {
            "id": "user-uuid-123",
            "email": "owner@example.com",
            "role": "authenticated",
        }
        if email_confirmed:
            user["email_confirmed_at"] = "2026-01-01T00:00:00Z"
        else:
            user["email_confirmed_at"] = None

        mock_resp.json.return_value = {
            "access_token": "eyJ.test.access",
            "refresh_token": "eyJ.test.refresh",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": user,
        }
        return mock_resp

    def _mock_owner_role_check(self):
        """Mock the _check_owner_role to return None (owner role confirmed)."""
        return patch.object(
            __import__("auth_ext.views", fromlist=["OwnerLoginView"]).OwnerLoginView,
            "_check_owner_role",
            return_value=None,
        )

    # ------------------------------------------------------------------
    # Email verification enforcement
    # ------------------------------------------------------------------

    def test_unconfirmed_email_returns_403_email_not_verified(self):
        """Supabase auth succeeds but email_confirmed_at is null → 403 email_not_verified."""
        mock_resp = self._mock_supabase_success(email_confirmed=False)

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "owner@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body.get("error"), "email_not_verified")
        self.assertIn("detail", body)

    def test_missing_email_confirmed_at_returns_403(self):
        """Supabase response missing email_confirmed_at field entirely → 403 email_not_verified."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "eyJ.test.access",
            "refresh_token": "eyJ.test.refresh",
            "token_type": "bearer",
            "expires_in": 3600,
            "user": {
                "id": "user-uuid-789",
                "email": "noemail@example.com",
                "role": "authenticated",
                # email_confirmed_at key is absent entirely
            },
        }

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "noemail@example.com", "password": "secret"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 403)
        body = resp.json()
        self.assertEqual(body.get("error"), "email_not_verified")
        self.assertIn("detail", body)

    def test_confirmed_email_does_not_return_403_for_email_check(self):
        """email_confirmed_at is set (non-null) → email check passes (proceeds to role check)."""
        mock_resp = self._mock_supabase_success(email_confirmed=True)

        # Mock the role check to avoid needing a real Supabase users table
        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            with patch("auth_ext.views.requests.get") as mock_get:
                mock_role_resp = MagicMock()
                mock_role_resp.json.return_value = [{"role": "owner"}]
                mock_get.return_value = mock_role_resp

                resp = self.client.post(
                    self.url,
                    data=json.dumps({"email": "owner@example.com", "password": "secret"}),
                    content_type="application/json",
                )

        # Should NOT be a 403 from the email check (may be 200 or other status)
        self.assertNotEqual(resp.status_code, 403)

    def test_email_check_happens_before_role_check(self):
        """If email not verified, role check (GET to Supabase) must NOT be called."""
        mock_resp = self._mock_supabase_success(email_confirmed=False)

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            with patch("auth_ext.views.requests.get") as mock_get:
                resp = self.client.post(
                    self.url,
                    data=json.dumps({"email": "owner@example.com", "password": "secret"}),
                    content_type="application/json",
                )

        self.assertEqual(resp.status_code, 403)
        # The role check (GET to Supabase) must NOT have been called
        mock_get.assert_not_called()
