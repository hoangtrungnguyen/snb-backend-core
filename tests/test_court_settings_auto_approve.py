"""
Tests for PATCH /api/courts/{id}/settings — auto-approve toggle (grava-3106.7).

BCORE-026 / OWNER-44

Acceptance criteria:
  - PATCH /api/courts/{id}/settings by the court owner with
    {"auto_approve_single": true|false} toggles the flag on the court row.
  - Returns the updated court settings: {"court_id": ..., "auto_approve_single": ...}
  - 401 if no/invalid auth.
  - 403 if authenticated user is not the owner of the court.
  - 404 if court does not exist.
  - 400 if auto_approve_single is missing or not a boolean.
  - GET /api/courts/{id}/ includes auto_approve_single in the response.
"""
import json
import uuid
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

OWNER_ID = str(uuid.uuid4())
OTHER_OWNER_ID = str(uuid.uuid4())
COURT_ID = str(uuid.uuid4())


def _owner_payload(uid=None):
    uid = uid or OWNER_ID
    return {"sub": uid, "email": "owner@example.com", "app_metadata": {"role": "owner"}}


def _court_row(court_id=None, owner_id=None, auto_approve_single=False, **overrides):
    court_id = court_id or COURT_ID
    owner_id = owner_id or OWNER_ID
    row = {
        "id": court_id,
        "owner_id": owner_id,
        "name": "Test Court",
        "slug": "test-court",
        "sport_types": ["football"],
        "capacity": 22,
        "price_per_hour": "50.00",
        "operating_hours": {"mon": {"open": "06:00", "close": "22:00"}},
        "address": "123 Main St",
        "lat": "10.123",
        "lng": "106.456",
        "status": "pending",
        "amenities": ["parking"],
        "description": "A test court",
        "photos": [],
        "auto_approve_single": auto_approve_single,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _supa_single(court_id=None, owner_id=None, **kw):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = [_court_row(court_id=court_id, owner_id=owner_id, **kw)]
    return r


def _supa_empty():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = []
    return r


def _supa_patch(court_id=None, owner_id=None, **kw):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = [_court_row(court_id=court_id, owner_id=owner_id, **kw)]
    return r


class TestCourtSettingsAutoApproveToggle(TestCase):
    """PATCH /api/courts/{id}/settings"""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/courts/{COURT_ID}/settings"

    def _auth_headers(self, payload=None):
        payload = payload or _owner_payload()
        with patch("auth_ext.middleware._decode_token", return_value=payload):
            pass
        return {"HTTP_AUTHORIZATION": "Bearer fake-token"}

    # ------------------------------------------------------------------
    # Happy path: enable auto-approve
    # ------------------------------------------------------------------

    @patch("courts.views.requests.get")
    @patch("courts.views.requests.patch")
    def test_enable_auto_approve(self, mock_patch, mock_get):
        """Owner toggles auto_approve_single to True — 200 returned."""
        mock_get.return_value = _supa_single(auto_approve_single=False)
        mock_patch.return_value = _supa_patch(auto_approve_single=True)

        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.patch(
                self.url,
                data=json.dumps({"auto_approve_single": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["court_id"], COURT_ID)
        self.assertIs(data["auto_approve_single"], True)

    # ------------------------------------------------------------------
    # Happy path: disable auto-approve
    # ------------------------------------------------------------------

    @patch("courts.views.requests.get")
    @patch("courts.views.requests.patch")
    def test_disable_auto_approve(self, mock_patch, mock_get):
        """Owner toggles auto_approve_single to False — 200 returned."""
        mock_get.return_value = _supa_single(auto_approve_single=True)
        mock_patch.return_value = _supa_patch(auto_approve_single=False)

        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.patch(
                self.url,
                data=json.dumps({"auto_approve_single": False}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["court_id"], COURT_ID)
        self.assertIs(data["auto_approve_single"], False)

    # ------------------------------------------------------------------
    # 401 — no auth
    # ------------------------------------------------------------------

    def test_no_auth_returns_401(self):
        """No Authorization header — 401."""
        resp = self.client.patch(
            self.url,
            data=json.dumps({"auto_approve_single": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------
    # 401 — invalid token
    # ------------------------------------------------------------------

    def test_invalid_token_returns_401(self):
        """Invalid JWT — 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self.client.patch(
                self.url,
                data=json.dumps({"auto_approve_single": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer bad-token",
            )
        self.assertEqual(resp.status_code, 401)

    # ------------------------------------------------------------------
    # 403 — authenticated but not owner role
    # ------------------------------------------------------------------

    def test_player_role_returns_403(self):
        """Authenticated player (not owner) — 403."""
        player_payload = {"sub": str(uuid.uuid4()), "email": "p@e.com", "app_metadata": {"role": "player"}}
        with patch("auth_ext.middleware._decode_token", return_value=player_payload):
            resp = self.client.patch(
                self.url,
                data=json.dumps({"auto_approve_single": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # 403 — owner but not court owner
    # ------------------------------------------------------------------

    @patch("courts.views.requests.get")
    def test_wrong_owner_returns_403(self, mock_get):
        """Owner of a different court — 403."""
        # Court belongs to OTHER_OWNER_ID
        mock_get.return_value = _supa_single(owner_id=OTHER_OWNER_ID)

        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload(OWNER_ID)):
            resp = self.client.patch(
                self.url,
                data=json.dumps({"auto_approve_single": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # 404 — court does not exist
    # ------------------------------------------------------------------

    @patch("courts.views.requests.get")
    def test_court_not_found_returns_404(self, mock_get):
        """Court does not exist — 404."""
        mock_get.return_value = _supa_empty()

        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.patch(
                self.url,
                data=json.dumps({"auto_approve_single": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # 400 — missing auto_approve_single field
    # ------------------------------------------------------------------

    @patch("courts.views.requests.get")
    def test_missing_field_returns_400(self, mock_get):
        """auto_approve_single not in body — 400."""
        mock_get.return_value = _supa_single()

        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.patch(
                self.url,
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("auto_approve_single", resp.json().get("error", ""))

    # ------------------------------------------------------------------
    # 400 — non-boolean value
    # ------------------------------------------------------------------

    @patch("courts.views.requests.get")
    def test_non_boolean_value_returns_400(self, mock_get):
        """auto_approve_single is not a boolean — 400."""
        mock_get.return_value = _supa_single()

        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.patch(
                self.url,
                data=json.dumps({"auto_approve_single": "yes"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # 400 — invalid JSON body
    # ------------------------------------------------------------------

    def test_invalid_json_returns_400(self):
        """Malformed JSON body — 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.patch(
                self.url,
                data="not-json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # 405 — wrong HTTP method
    # ------------------------------------------------------------------

    def test_get_method_returns_405(self):
        """GET on /settings is not allowed — 405."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.get(
                self.url,
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 405)

    def test_post_method_returns_405(self):
        """POST on /settings is not allowed — 405."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            resp = self.client.post(
                self.url,
                data=json.dumps({"auto_approve_single": True}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer fake-token",
            )
        self.assertEqual(resp.status_code, 405)


class TestCourtDetailIncludesAutoApprove(TestCase):
    """GET /api/courts/{id}/ includes auto_approve_single in response."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/courts/{COURT_ID}/"

    @patch("courts.views.requests.get")
    def test_court_detail_includes_auto_approve_field(self, mock_get):
        """GET /api/courts/{id}/ response includes auto_approve_single."""
        mock_get.return_value = _supa_single(auto_approve_single=True)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("auto_approve_single", data)
        self.assertIs(data["auto_approve_single"], True)

    @patch("courts.views.requests.get")
    def test_court_detail_auto_approve_defaults_false(self, mock_get):
        """auto_approve_single defaults to False when not set."""
        mock_get.return_value = _supa_single(auto_approve_single=False)

        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("auto_approve_single", data)
        self.assertIs(data["auto_approve_single"], False)
