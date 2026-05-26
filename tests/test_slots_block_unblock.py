"""
Tests for PATCH /api/courts/slots/{id}/block and PATCH /api/courts/slots/{id}/unblock
(grava-3106.3).

Covers all subtasks:
  grava-3106.3.1 -- PATCH /slots/{id}/block — sets status=blocked, stores blocked_reason;
                    returns 409 if status=booked
  grava-3106.3.2 -- PATCH /slots/{id}/unblock — sets status=open
  grava-3106.3.3 -- Supabase Realtime broadcasts slot row change automatically (DB-level,
                    no extra Django code — covered by integration contract tests)

All Supabase HTTP calls are mocked — no real network requests.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OTHER_OWNER_ID = "eeeeeeee-0000-0000-0000-000000000005"
_COURT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_SLOT_ID = "cccccccc-0000-0000-0000-000000000003"

_OWNER_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_OTHER_OWNER_PAYLOAD = {
    "sub": _OTHER_OWNER_ID,
    "email": "other@example.com",
    "app_metadata": {"role": "owner"},
}

_PLAYER_PAYLOAD = {
    "sub": "dddddddd-0000-0000-0000-000000000004",
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OPEN_SLOT_ROW = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": "2026-05-25T10:00:00Z",
    "end_at": "2026-05-25T12:00:00Z",
    "status": "open",
    "is_owner_slot": False,
    "access_policy": None,
    "max_players": None,
    "blocked_reason": None,
    "created_at": "2026-05-26T00:00:00Z",
    "updated_at": "2026-05-26T00:00:00Z",
}

_BOOKED_SLOT_ROW = dict(_OPEN_SLOT_ROW, status="booked")
_BLOCKED_SLOT_ROW = dict(_OPEN_SLOT_ROW, status="blocked")
_COURT_ROW = {"id": _COURT_ID, "owner_id": _OWNER_ID}


def _mock_resp(status_code: int, data):
    """Build a mock requests.Response."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


def _slot_resp(row):
    return _mock_resp(200, [row])


def _no_slot_resp():
    return _mock_resp(200, [])


def _court_resp(owner_id=_OWNER_ID):
    return _mock_resp(200, [{"id": _COURT_ID, "owner_id": owner_id}])


def _patch_slot_resp(row):
    """Supabase PATCH returns updated row in a list."""
    return _mock_resp(200, [row])


# ---------------------------------------------------------------------------
# PATCH /api/courts/slots/{id}/block
# ---------------------------------------------------------------------------

class SlotBlockTests(TestCase):
    """Tests for PATCH /api/courts/slots/{id}/block (grava-3106.3.1)."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/courts/slots/{_SLOT_ID}/block"

    def _patch(self, body=None, token="owner.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body or {}),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.patch(self.url, **kwargs)

    # ------------------------------------------------------------------
    # Authentication / authorisation
    # ------------------------------------------------------------------

    def test_no_auth_header_returns_401(self):
        """No Authorization header → 401."""
        resp = self._patch(auth=False)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._patch()
        self.assertEqual(resp.status_code, 401)

    def test_player_role_returns_403(self):
        """Player (non-owner) → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._patch()
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Slot not found
    # ------------------------------------------------------------------

    def test_slot_not_found_returns_404(self):
        """Slot ID doesn't exist → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_no_slot_resp()):
            resp = self._patch()
        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.json().get("error", "").lower())

    # ------------------------------------------------------------------
    # Ownership check (slot → court → owner)
    # ------------------------------------------------------------------

    def test_non_owner_of_court_returns_403(self):
        """Owner who doesn't own the court → 403."""
        # Slot belongs to a court owned by _OTHER_OWNER_ID
        slot_row = dict(_OPEN_SLOT_ROW)

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(slot_row)
            # court fetch: different owner
            return _court_resp(owner_id=_OTHER_OWNER_ID)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            resp = self._patch()
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # 409 — cannot block a booked slot
    # ------------------------------------------------------------------

    def test_blocking_booked_slot_returns_409(self):
        """Slot with status=booked → 409 Conflict."""

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_BOOKED_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            resp = self._patch({"blocked_reason": "Maintenance"})
        self.assertEqual(resp.status_code, 409)
        error = resp.json().get("error", "")
        # Error message about not being able to block a booked slot
        self.assertTrue(
            "booking" in error.lower() or "booked" in error.lower(),
            f"Expected booking-related error, got: {error}",
        )

    # ------------------------------------------------------------------
    # Happy path — block an open slot
    # ------------------------------------------------------------------

    def test_block_open_slot_returns_200(self):
        """Block an open slot → 200 with updated slot data."""
        updated_row = dict(_OPEN_SLOT_ROW, status="blocked", blocked_reason="Private event")

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_OPEN_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch({"blocked_reason": "Private event"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "blocked")

    def test_block_sets_status_to_blocked(self):
        """PATCH /block must send status=blocked to Supabase."""
        updated_row = dict(_OPEN_SLOT_ROW, status="blocked")
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_OPEN_SLOT_ROW)
            return _court_resp()

        def capture_patch(*args, **kwargs):
            captured.update(kwargs)
            return _patch_slot_resp(updated_row)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", side_effect=capture_patch):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)
        sent = captured.get("json", {})
        self.assertEqual(sent.get("status"), "blocked")

    def test_block_stores_blocked_reason(self):
        """blocked_reason is sent to Supabase when provided."""
        updated_row = dict(_OPEN_SLOT_ROW, status="blocked", blocked_reason="Staff training")
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_OPEN_SLOT_ROW)
            return _court_resp()

        def capture_patch(*args, **kwargs):
            captured.update(kwargs)
            return _patch_slot_resp(updated_row)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", side_effect=capture_patch):
            resp = self._patch({"blocked_reason": "Staff training"})
        self.assertEqual(resp.status_code, 200)
        sent = captured.get("json", {})
        self.assertEqual(sent.get("blocked_reason"), "Staff training")

    def test_block_without_reason_is_allowed(self):
        """blocked_reason is optional — no body or empty reason → 200."""
        updated_row = dict(_OPEN_SLOT_ROW, status="blocked")

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_OPEN_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch({})
        self.assertEqual(resp.status_code, 200)

    def test_block_already_blocked_slot_is_idempotent(self):
        """Blocking an already-blocked slot → 200 (idempotent)."""
        updated_row = dict(_BLOCKED_SLOT_ROW)

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_BLOCKED_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)

    def test_block_maintenance_slot_is_allowed(self):
        """Blocking a slot with status=maintenance → 200 (not booked, OK)."""
        maintenance_row = dict(_OPEN_SLOT_ROW, status="maintenance")
        updated_row = dict(_OPEN_SLOT_ROW, status="blocked")

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(maintenance_row)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)

    def test_response_includes_slot_fields(self):
        """Response body includes all standard slot fields."""
        updated_row = dict(_OPEN_SLOT_ROW, status="blocked", blocked_reason="Test")

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_OPEN_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch({"blocked_reason": "Test"})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for field in ("id", "court_id", "start_at", "end_at", "status", "blocked_reason"):
            self.assertIn(field, body, f"Missing field: {field}")

    # ------------------------------------------------------------------
    # Service errors
    # ------------------------------------------------------------------

    def test_supabase_slot_fetch_failure_returns_503(self):
        """Supabase slot fetch fails → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._patch()
        self.assertEqual(resp.status_code, 503)

    def test_supabase_patch_failure_returns_503(self):
        """Supabase PATCH fails → 503."""
        import requests as req_lib

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_OPEN_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", side_effect=req_lib.RequestException("timeout")):
            resp = self._patch()
        self.assertEqual(resp.status_code, 503)

    # ------------------------------------------------------------------
    # HTTP method guard
    # ------------------------------------------------------------------

    def test_get_method_returns_405(self):
        """GET /slots/{id}/block → 405."""
        resp = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer token")
        self.assertEqual(resp.status_code, 405)

    def test_post_method_returns_405(self):
        """POST /slots/{id}/block → 405."""
        resp = self.client.post(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer token",
        )
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# PATCH /api/courts/slots/{id}/unblock
# ---------------------------------------------------------------------------

class SlotUnblockTests(TestCase):
    """Tests for PATCH /api/courts/slots/{id}/unblock (grava-3106.3.2)."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/courts/slots/{_SLOT_ID}/unblock"

    def _patch(self, body=None, token="owner.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body or {}),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.patch(self.url, **kwargs)

    # ------------------------------------------------------------------
    # Authentication / authorisation
    # ------------------------------------------------------------------

    def test_no_auth_header_returns_401(self):
        """No Authorization header → 401."""
        resp = self._patch(auth=False)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._patch()
        self.assertEqual(resp.status_code, 401)

    def test_player_role_returns_403(self):
        """Player (non-owner) → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._patch()
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Slot not found
    # ------------------------------------------------------------------

    def test_slot_not_found_returns_404(self):
        """Slot ID doesn't exist → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_no_slot_resp()):
            resp = self._patch()
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # Ownership check
    # ------------------------------------------------------------------

    def test_non_owner_of_court_returns_403(self):
        """Owner who doesn't own the court → 403."""
        slot_row = dict(_BLOCKED_SLOT_ROW)

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(slot_row)
            return _court_resp(owner_id=_OTHER_OWNER_ID)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            resp = self._patch()
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Happy path — unblock a blocked slot
    # ------------------------------------------------------------------

    def test_unblock_blocked_slot_returns_200(self):
        """Unblock a blocked slot → 200 with updated slot data."""
        updated_row = dict(_BLOCKED_SLOT_ROW, status="open", blocked_reason=None)

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_BLOCKED_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "open")

    def test_unblock_sets_status_to_open(self):
        """PATCH /unblock must send status=open to Supabase."""
        updated_row = dict(_BLOCKED_SLOT_ROW, status="open")
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_BLOCKED_SLOT_ROW)
            return _court_resp()

        def capture_patch(*args, **kwargs):
            captured.update(kwargs)
            return _patch_slot_resp(updated_row)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", side_effect=capture_patch):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)
        sent = captured.get("json", {})
        self.assertEqual(sent.get("status"), "open")

    def test_unblock_clears_blocked_reason(self):
        """Unblock should clear blocked_reason (set to null)."""
        updated_row = dict(_BLOCKED_SLOT_ROW, status="open", blocked_reason=None)
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(dict(_BLOCKED_SLOT_ROW, blocked_reason="Some reason"))
            return _court_resp()

        def capture_patch(*args, **kwargs):
            captured.update(kwargs)
            return _patch_slot_resp(updated_row)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", side_effect=capture_patch):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)
        sent = captured.get("json", {})
        self.assertIsNone(sent.get("blocked_reason"))

    def test_unblock_open_slot_is_idempotent(self):
        """Unblocking an already-open slot → 200 (idempotent)."""
        updated_row = dict(_OPEN_SLOT_ROW)

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_OPEN_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)

    def test_response_slot_appears_available_to_players(self):
        """After unblock, slot status=open (re-appears in player slot picker)."""
        updated_row = dict(_BLOCKED_SLOT_ROW, status="open")

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_BLOCKED_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "open")

    def test_response_includes_slot_fields(self):
        """Response body includes all standard slot fields."""
        updated_row = dict(_BLOCKED_SLOT_ROW, status="open", blocked_reason=None)

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_BLOCKED_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", return_value=_patch_slot_resp(updated_row)):
            resp = self._patch()
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        for field in ("id", "court_id", "start_at", "end_at", "status", "blocked_reason"):
            self.assertIn(field, body, f"Missing field: {field}")

    # ------------------------------------------------------------------
    # Service errors
    # ------------------------------------------------------------------

    def test_supabase_slot_fetch_failure_returns_503(self):
        """Supabase slot fetch fails → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._patch()
        self.assertEqual(resp.status_code, 503)

    def test_supabase_patch_failure_returns_503(self):
        """Supabase PATCH fails → 503."""
        import requests as req_lib

        def get_side_effect(url, params=None, **kwargs):
            if "slots" in url:
                return _slot_resp(_BLOCKED_SLOT_ROW)
            return _court_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.patch", side_effect=req_lib.RequestException("timeout")):
            resp = self._patch()
        self.assertEqual(resp.status_code, 503)

    # ------------------------------------------------------------------
    # HTTP method guard
    # ------------------------------------------------------------------

    def test_get_method_returns_405(self):
        """GET /slots/{id}/unblock → 405."""
        resp = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer token")
        self.assertEqual(resp.status_code, 405)

    def test_post_method_returns_405(self):
        """POST /slots/{id}/unblock → 405."""
        resp = self.client.post(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer token",
        )
        self.assertEqual(resp.status_code, 405)
