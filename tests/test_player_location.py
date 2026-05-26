"""
Tests for PATCH /api/players/me/location (grava-5044.4 — BCORE-063).

Subtasks covered:
  grava-5044.4.1  PATCH /players/me/location — updates users.last_lat, users.last_lng
  grava-5044.4.2  Called by client app on map screen open
  grava-5044.4.3  No history stored; only current location (only last_lat, last_lng updated)
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


class PlayerLocationPatchTests(TestCase):
    """Tests for PATCH /api/players/me/location endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/players/me/location"
        self.user_id = "550e8400-e29b-41d4-a716-446655440000"
        self.valid_token = "eyJ.valid.token"
        self.mock_payload = {
            "sub": self.user_id,
            "email": "player@example.com",
            "app_metadata": {"role": "player"},
        }

    def _mock_supabase_patch_ok(self):
        """Mock a successful Supabase PATCH response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {
                "id": self.user_id,
                "last_lat": "10.7769",
                "last_lng": "106.7009",
            }
        ]
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
        """PATCH without Authorization header -> 401."""
        resp = self.client.patch(
            self.url,
            data=json.dumps({"lat": 10.77, "lng": 106.70}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """PATCH with invalid JWT -> 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._patch_request({"lat": 10.77, "lng": 106.70}, token="bad.jwt")
        self.assertEqual(resp.status_code, 401)

    def test_non_player_role_returns_403(self):
        """PATCH by a non-player (e.g. owner) -> 403."""
        owner_payload = {
            "sub": self.user_id,
            "email": "owner@example.com",
            "app_metadata": {"role": "owner"},
        }
        with patch("auth_ext.middleware._decode_token", return_value=owner_payload):
            resp = self._patch_request({"lat": 10.77, "lng": 106.70})
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # 400 — invalid body
    # ------------------------------------------------------------------

    def test_missing_lat_returns_400(self):
        """PATCH without lat -> 400."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self._patch_request({"lng": 106.70})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("lat", resp.json()["error"])

    def test_missing_lng_returns_400(self):
        """PATCH without lng -> 400."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self._patch_request({"lat": 10.77})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("lng", resp.json()["error"])

    def test_non_numeric_lat_returns_400(self):
        """PATCH with lat='abc' -> 400."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self._patch_request({"lat": "abc", "lng": 106.70})
        self.assertEqual(resp.status_code, 400)

    def test_non_numeric_lng_returns_400(self):
        """PATCH with lng='abc' -> 400."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self._patch_request({"lat": 10.77, "lng": "abc"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        """PATCH with non-JSON body -> 400."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self.client.patch(
                self.url,
                data="not json",
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.valid_token}",
            )
        self.assertEqual(resp.status_code, 400)

    def test_lat_out_of_range_returns_400(self):
        """lat must be in [-90, 90]."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self._patch_request({"lat": 91.0, "lng": 106.70})
        self.assertEqual(resp.status_code, 400)

    def test_lng_out_of_range_returns_400(self):
        """lng must be in [-180, 180]."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self._patch_request({"lat": 10.77, "lng": 181.0})
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_returns_200_with_location(self):
        """Valid PATCH -> 200 with updated lat/lng."""
        mock_resp = self._mock_supabase_patch_ok()

        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_resp):
            resp = self._patch_request({"lat": 10.7769, "lng": 106.7009})

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("last_lat", body)
        self.assertIn("last_lng", body)

    def test_only_last_lat_last_lng_sent_to_supabase(self):
        """
        Only last_lat and last_lng (and optionally location_updated_at)
        are sent to Supabase — no history table, no other fields.
        grava-5044.4.3
        """
        mock_resp = self._mock_supabase_patch_ok()
        captured = {}

        def capture_patch(*args, **kwargs):
            captured.update(kwargs)
            return mock_resp

        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload), \
             patch("players.views.requests.patch", side_effect=capture_patch):
            resp = self._patch_request({"lat": 10.7769, "lng": 106.7009})

        self.assertEqual(resp.status_code, 200)
        sent = captured.get("json", {})
        # Must include last_lat and last_lng
        self.assertIn("last_lat", sent)
        self.assertIn("last_lng", sent)
        # Must NOT include any history-like field
        self.assertNotIn("location_history", sent)
        self.assertNotIn("previous_lat", sent)
        self.assertNotIn("previous_lng", sent)
        # Values must match the input
        self.assertAlmostEqual(float(sent["last_lat"]), 10.7769, places=3)
        self.assertAlmostEqual(float(sent["last_lng"]), 106.7009, places=3)

    def test_integer_lat_lng_accepted(self):
        """Integer lat/lng values should be accepted."""
        mock_resp = self._mock_supabase_patch_ok()

        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_resp):
            resp = self._patch_request({"lat": 10, "lng": 106})

        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # 404 — user not found
    # ------------------------------------------------------------------

    def test_user_not_found_returns_404(self):
        """Supabase returns empty array -> 404."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []

        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_resp):
            resp = self._patch_request({"lat": 10.77, "lng": 106.70})

        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # 503 — network/upstream errors
    # ------------------------------------------------------------------

    def test_network_error_returns_503(self):
        """Network failure on Supabase PATCH -> 503."""
        import requests as req_lib

        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload), \
             patch("players.views.requests.patch",
                   side_effect=req_lib.RequestException("timeout")):
            resp = self._patch_request({"lat": 10.77, "lng": 106.70})

        self.assertEqual(resp.status_code, 503)

    def test_supabase_non_200_returns_503(self):
        """Supabase PATCH returns non-200 -> 503."""
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.json.return_value = {"error": "internal"}

        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload), \
             patch("players.views.requests.patch", return_value=mock_resp):
            resp = self._patch_request({"lat": 10.77, "lng": 106.70})

        self.assertEqual(resp.status_code, 503)

    # ------------------------------------------------------------------
    # HTTP method guard
    # ------------------------------------------------------------------

    def test_get_returns_405(self):
        """GET on this endpoint -> 405."""
        with patch("auth_ext.middleware._decode_token", return_value=self.mock_payload):
            resp = self.client.get(
                self.url, HTTP_AUTHORIZATION=f"Bearer {self.valid_token}"
            )
        self.assertEqual(resp.status_code, 405)
