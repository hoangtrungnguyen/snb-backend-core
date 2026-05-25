"""
Tests for GET /api/players/me endpoint.

Mocks Supabase HTTP calls and JWT authentication — no real network requests.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


class PlayersMeViewTests(TestCase):
    """Tests for GET /api/players/me endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/players/me"
        self.user_id = "550e8400-e29b-41d4-a716-446655440000"
        self.valid_token = "eyJ.valid.token"

    def _mock_supabase_user_response(self, user_data=None):
        """Build a mock Supabase REST API response for a user record."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if user_data is None:
            user_data = [
                {
                    "id": self.user_id,
                    "email": "player@example.com",
                    "name": "Test Player",
                    "phone": "+1234567890",
                    "role": "player",
                }
            ]
        mock_resp.json.return_value = user_data
        return mock_resp

    def _make_authenticated_request(self, token=None):
        """Make GET request with Authorization header."""
        if token is None:
            token = self.valid_token
        return self.client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    # ------------------------------------------------------------------
    # Authentication checks
    # ------------------------------------------------------------------

    def test_unauthenticated_returns_401(self):
        """Request without Authorization header → 401."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Request with invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._make_authenticated_request(token="invalid.token")
        self.assertIn(resp.status_code, [401, 403])

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_returns_player_profile_fields(self):
        """Valid auth + existing user → 200 with id, email, name, phone."""
        mock_supabase_resp = self._mock_supabase_user_response()
        mock_payload = {
            "sub": self.user_id,
            "email": "player@example.com",
            "app_metadata": {"role": "player"},
        }

        with patch("auth_ext.middleware._decode_token", return_value=mock_payload), \
             patch("players.views.requests.get", return_value=mock_supabase_resp):
            resp = self._make_authenticated_request()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("id", body)
        self.assertIn("email", body)
        self.assertIn("name", body)
        self.assertIn("phone", body)
        self.assertEqual(body["id"], self.user_id)
        self.assertEqual(body["email"], "player@example.com")
        self.assertEqual(body["name"], "Test Player")
        self.assertEqual(body["phone"], "+1234567890")

    def test_returns_only_get_method(self):
        """POST request → 405 Method Not Allowed."""
        mock_payload = {
            "sub": self.user_id,
            "email": "player@example.com",
            "app_metadata": {"role": "player"},
        }
        with patch("auth_ext.middleware._decode_token", return_value=mock_payload):
            resp = self.client.post(
                self.url,
                HTTP_AUTHORIZATION=f"Bearer {self.valid_token}",
            )
        self.assertEqual(resp.status_code, 405)

    # ------------------------------------------------------------------
    # 403 — non-player role
    # ------------------------------------------------------------------

    def test_non_player_role_returns_403(self):
        """Authenticated user without player role → 403."""
        mock_payload = {
            "sub": self.user_id,
            "email": "owner@example.com",
            "app_metadata": {"role": "owner"},
        }
        with patch("auth_ext.middleware._decode_token", return_value=mock_payload):
            resp = self._make_authenticated_request()
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # 404 — user not found
    # ------------------------------------------------------------------

    def test_user_not_in_users_table_returns_404(self):
        """Authenticated user with no record in public.users → 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []  # empty result from Supabase
        mock_payload = {
            "sub": self.user_id,
            "email": "player@example.com",
            "app_metadata": {"role": "player"},
        }

        with patch("auth_ext.middleware._decode_token", return_value=mock_payload), \
             patch("players.views.requests.get", return_value=mock_resp):
            resp = self._make_authenticated_request()

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertIn("error", body)

    # ------------------------------------------------------------------
    # 503 — network error
    # ------------------------------------------------------------------

    def test_network_error_returns_503(self):
        """Network failure fetching from Supabase → 503."""
        import requests as req_lib
        mock_payload = {
            "sub": self.user_id,
            "email": "player@example.com",
            "app_metadata": {"role": "player"},
        }

        with patch("auth_ext.middleware._decode_token", return_value=mock_payload), \
             patch("players.views.requests.get", side_effect=req_lib.RequestException("Connection refused")):
            resp = self._make_authenticated_request()

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        # No internal details in response body
        self.assertNotIn("Connection refused", json.dumps(body))

    # ------------------------------------------------------------------
    # No internal error details leaked
    # ------------------------------------------------------------------

    def test_503_response_has_no_internal_details(self):
        """503 response must not expose internal error details."""
        import requests as req_lib
        mock_payload = {
            "sub": self.user_id,
            "email": "player@example.com",
            "app_metadata": {"role": "player"},
        }

        with patch("auth_ext.middleware._decode_token", return_value=mock_payload), \
             patch("players.views.requests.get", side_effect=req_lib.RequestException("secret internal error")):
            resp = self._make_authenticated_request()

        body_text = resp.content.decode("utf-8")
        self.assertNotIn("secret internal error", body_text)
        self.assertNotIn("Traceback", body_text)
