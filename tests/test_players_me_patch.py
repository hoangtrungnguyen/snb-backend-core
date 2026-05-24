"""
Tests for PATCH /api/players/me endpoint.

Mocks Supabase HTTP calls and JWT authentication — no real network requests.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


class PlayersMePatchTests(TestCase):
    """Tests for PATCH /api/players/me endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/players/me"
        self.user_id = "550e8400-e29b-41d4-a716-446655440000"
        self.valid_token = "eyJ.valid.token"
        self.mock_payload = {
            "sub": self.user_id,
            "email": "player@example.com",
            "role": "authenticated",
            "user_metadata": {"role": "player"},
        }

    def _mock_supabase_patch_response(self, user_data=None):
        """Build a mock Supabase REST API PATCH response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if user_data is None:
            user_data = [
                {
                    "id": self.user_id,
                    "email": "player@example.com",
                    "name": "Updated Name",
                    "phone": "+1234567890",
                    "role": "player",
                }
            ]
        mock_resp.json.return_value = user_data
        return mock_resp

    def _patch_request(self, body, token=None):
        """Make PATCH request with Authorization header and JSON body."""
        if token is None:
            token = self.valid_token
        return self.client.patch(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    # ------------------------------------------------------------------
    # Authentication checks
    # ------------------------------------------------------------------

    def test_unauthenticated_returns_401(self):
        """PATCH without Authorization header → 401."""
        resp = self.client.patch(
            self.url,
            data=json.dumps({"full_name": "John Doe"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """PATCH with invalid JWT → 401."""
        with patch("auth_ext.authentication.jwt.decode") as mock_decode:
            mock_decode.side_effect = Exception("Invalid token")
            resp = self._patch_request({"full_name": "John Doe"}, token="bad.token")
        self.assertIn(resp.status_code, [401, 403])

    # ------------------------------------------------------------------
    # 400 — invalid body
    # ------------------------------------------------------------------

    def test_missing_full_name_returns_400(self):
        """PATCH without full_name in body → 400."""
        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload):
            resp = self._patch_request({})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)

    def test_empty_full_name_returns_400(self):
        """PATCH with empty string full_name → 400."""
        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload):
            resp = self._patch_request({"full_name": ""})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)

    def test_whitespace_only_full_name_returns_400(self):
        """PATCH with whitespace-only full_name → 400."""
        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload):
            resp = self._patch_request({"full_name": "   "})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)

    def test_non_string_full_name_returns_400(self):
        """PATCH with non-string full_name → 400."""
        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload):
            resp = self._patch_request({"full_name": 123})
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)

    def test_invalid_json_body_returns_400(self):
        """PATCH with non-JSON body → 400."""
        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload):
            resp = self.client.patch(
                self.url,
                data="not json",
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.valid_token}",
            )
        self.assertEqual(resp.status_code, 400)

    def test_extra_fields_are_ignored(self):
        """PATCH with extra immutable fields (id, email, role) → only full_name is updated."""
        mock_patch_resp = self._mock_supabase_patch_response()

        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_patch_resp):
            resp = self._patch_request({
                "full_name": "Updated Name",
                "id": "other-id",
                "email": "hacker@example.com",
                "role": "admin",
            })

        # Should succeed (extra fields silently ignored)
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_returns_200_with_updated_profile(self):
        """Valid PATCH → 200 with updated profile fields."""
        mock_patch_resp = self._mock_supabase_patch_response()

        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_patch_resp):
            resp = self._patch_request({"full_name": "Updated Name"})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("id", body)
        self.assertIn("email", body)
        self.assertIn("name", body)
        self.assertEqual(body["name"], "Updated Name")

    def test_only_updates_name_column_in_supabase(self):
        """Only `name` column is sent to Supabase — no other fields."""
        mock_patch_resp = self._mock_supabase_patch_response()
        captured_kwargs = {}

        def capture_patch(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return mock_patch_resp

        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload), \
             patch("players.views.requests.patch", side_effect=capture_patch):
            resp = self._patch_request({"full_name": "Alice"})

        self.assertEqual(resp.status_code, 200)
        # The json body sent to Supabase must only have "name"
        sent_json = captured_kwargs.get("json", {})
        self.assertIn("name", sent_json)
        self.assertNotIn("id", sent_json)
        self.assertNotIn("email", sent_json)
        self.assertNotIn("role", sent_json)
        self.assertEqual(sent_json["name"], "Alice")

    # ------------------------------------------------------------------
    # 404 — user not found
    # ------------------------------------------------------------------

    def test_user_not_found_returns_404(self):
        """Supabase returns empty array → 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_resp):
            resp = self._patch_request({"full_name": "Ghost"})

        self.assertEqual(resp.status_code, 404)
        body = resp.json()
        self.assertIn("error", body)

    # ------------------------------------------------------------------
    # 503 — network error
    # ------------------------------------------------------------------

    def test_network_error_returns_503(self):
        """Network failure on Supabase PATCH → 503."""
        import requests as req_lib

        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload), \
             patch("players.views.requests.patch", side_effect=req_lib.RequestException("timeout")):
            resp = self._patch_request({"full_name": "Someone"})

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn("timeout", json.dumps(body))

    def test_supabase_non_200_returns_503(self):
        """Supabase PATCH returns non-200 → 503."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "internal"}

        with patch("auth_ext.authentication.jwt.decode", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_resp):
            resp = self._patch_request({"full_name": "Someone"})

        self.assertEqual(resp.status_code, 503)
