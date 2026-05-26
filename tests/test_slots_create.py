"""
Tests for POST /api/courts/slots endpoint (grava-3106.2).

Covers all 4 subtasks:
  grava-3106.2.1 -- POST /slots: {court_id, start_at, end_at, status}
  grava-3106.2.2 -- Validates start_at/end_at within court operating_hours
  grava-3106.2.3 -- No overlapping slot (409 Slot conflict)
  grava-3106.2.4 -- is_owner_slot=true -> status=blocked, skip payment

All Supabase HTTP calls are mocked -- no real network requests.
"""
import json
from unittest.mock import patch, MagicMock, call

from django.test import TestCase, Client


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_COURT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_SLOT_ID = "cccccccc-0000-0000-0000-000000000003"

_OWNER_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_PLAYER_PAYLOAD = {
    "sub": "dddddddd-0000-0000-0000-000000000004",
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "operating_hours": {
        "mon": {"open": "08:00", "close": "22:00"},
        "tue": {"open": "08:00", "close": "22:00"},
        "wed": {"open": "08:00", "close": "22:00"},
        "thu": {"open": "08:00", "close": "22:00"},
        "fri": {"open": "08:00", "close": "22:00"},
        "sat": {"open": "09:00", "close": "21:00"},
        "sun": {"open": "09:00", "close": "21:00"},
    },
}

# A Monday slot (2026-05-25 is a Monday) well within operating hours
_VALID_START = "2026-05-25T10:00:00Z"
_VALID_END = "2026-05-25T12:00:00Z"

_SLOT_ROW = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": _VALID_START,
    "end_at": _VALID_END,
    "status": "open",
    "is_owner_slot": False,
    "access_policy": "open",
    "max_players": None,
    "blocked_reason": None,
    "created_at": "2026-05-26T00:00:00Z",
    "updated_at": "2026-05-26T00:00:00Z",
}


def _mock_resp(status_code: int, data):
    """Build a mock requests.Response."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


def _court_resp():
    return _mock_resp(200, [_COURT_ROW])


def _no_overlap_resp():
    """Supabase returns empty list — no overlapping slots."""
    return _mock_resp(200, [])


def _overlap_resp():
    """Supabase returns one overlapping slot."""
    return _mock_resp(200, [{"id": "existing-slot-id"}])


def _slot_created_resp():
    return _mock_resp(201, [_SLOT_ROW])


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class SlotsCreateTests(TestCase):
    """Tests for POST /api/courts/slots."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/slots"

    def _post(self, body, token="owner.jwt.token", auth=True):
        """Helper: POST with optional Bearer token."""
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    # ------------------------------------------------------------------
    # grava-3106.2.1 — Authentication / authorisation
    # ------------------------------------------------------------------

    def test_no_auth_header_returns_401(self):
        """No Authorization header → 401."""
        resp = self._post(
            {"court_id": _COURT_ID, "start_at": _VALID_START, "end_at": _VALID_END},
            auth=False,
        )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post(
                {"court_id": _COURT_ID, "start_at": _VALID_START, "end_at": _VALID_END}
            )
        self.assertEqual(resp.status_code, 401)

    def test_player_role_returns_403(self):
        """Player (non-owner) → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post(
                {"court_id": _COURT_ID, "start_at": _VALID_START, "end_at": _VALID_END}
            )
        self.assertEqual(resp.status_code, 403)

    def test_invalid_json_body_returns_400(self):
        """Malformed JSON body → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.post(
                self.url,
                data="not-json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner.jwt.token",
            )
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # grava-3106.2.1 — Required field validation
    # ------------------------------------------------------------------

    def test_missing_court_id_returns_400(self):
        """Missing court_id → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({"start_at": _VALID_START, "end_at": _VALID_END})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("court_id", resp.json().get("error", ""))

    def test_missing_start_at_returns_400(self):
        """Missing start_at → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({"court_id": _COURT_ID, "end_at": _VALID_END})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("start_at", resp.json().get("error", ""))

    def test_missing_end_at_returns_400(self):
        """Missing end_at → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({"court_id": _COURT_ID, "start_at": _VALID_START})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("end_at", resp.json().get("error", ""))

    def test_invalid_start_at_format_returns_400(self):
        """Non-ISO start_at → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": "not-a-datetime",
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 400)

    def test_invalid_end_at_format_returns_400(self):
        """Non-ISO end_at → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": "not-a-datetime",
            })
        self.assertEqual(resp.status_code, 400)

    def test_end_at_before_start_at_returns_400(self):
        """end_at ≤ start_at → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_END,
                "end_at": _VALID_START,
            })
        self.assertEqual(resp.status_code, 400)

    def test_end_at_equal_start_at_returns_400(self):
        """end_at == start_at → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_START,
            })
        self.assertEqual(resp.status_code, 400)

    def test_invalid_status_returns_400(self):
        """Unknown status value → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[_court_resp()]):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
                "status": "invalid_status",
            })
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # Court not found
    # ------------------------------------------------------------------

    def test_court_not_found_returns_404(self):
        """court_id references a non-existent court → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_mock_resp(200, [])):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 404)

    # ------------------------------------------------------------------
    # grava-3106.2.1 — Happy path: create slot
    # ------------------------------------------------------------------

    def test_valid_slot_returns_201(self):
        """Valid owner request → 201 with slot data."""
        get_responses = [_court_resp(), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", return_value=_slot_created_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertIn("id", body)
        self.assertIn("court_id", body)
        self.assertIn("start_at", body)
        self.assertIn("end_at", body)
        self.assertIn("status", body)

    def test_response_includes_is_owner_slot_field(self):
        """Response body always includes is_owner_slot."""
        get_responses = [_court_resp(), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", return_value=_slot_created_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 201)
        self.assertIn("is_owner_slot", resp.json())

    def test_explicit_open_status_accepted(self):
        """Explicit status=open is accepted and sent to Supabase."""
        slot_row = dict(_SLOT_ROW, status="open")
        get_responses = [_court_resp(), _no_overlap_resp()]
        captured = {}

        def capture_post(*args, **kwargs):
            captured.update(kwargs)
            return _mock_resp(201, [slot_row])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", side_effect=capture_post):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
                "status": "open",
            })
        self.assertEqual(resp.status_code, 201)
        sent = captured.get("json", {})
        self.assertEqual(sent.get("status"), "open")

    # ------------------------------------------------------------------
    # grava-3106.2.2 — Operating hours validation
    # ------------------------------------------------------------------

    def test_slot_before_opening_time_returns_400(self):
        """start_at before court open time → 400."""
        # 07:00 is before 08:00 open on Monday
        early_start = "2026-05-25T07:00:00Z"
        early_end = "2026-05-25T09:00:00Z"
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_court_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": early_start,
                "end_at": early_end,
            })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("08:00", resp.json().get("error", ""))

    def test_slot_after_closing_time_returns_400(self):
        """end_at after court close time → 400."""
        # 22:30 is after 22:00 close on Monday
        late_start = "2026-05-25T21:00:00Z"
        late_end = "2026-05-25T22:30:00Z"
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_court_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": late_start,
                "end_at": late_end,
            })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("22:00", resp.json().get("error", ""))

    def test_slot_on_closed_day_returns_400(self):
        """Slot on a day not in operating_hours → 400."""
        court_with_partial_hours = dict(_COURT_ROW, operating_hours={
            "mon": {"open": "08:00", "close": "22:00"},
        })
        # 2026-05-26 is a Tuesday — not in operating_hours
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_mock_resp(200, [court_with_partial_hours])):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": "2026-05-26T10:00:00Z",
                "end_at": "2026-05-26T12:00:00Z",
            })
        self.assertEqual(resp.status_code, 400)
        # Error message mentions the day
        error = resp.json().get("error", "").lower()
        self.assertIn("tue", error)

    def test_no_operating_hours_allows_any_time(self):
        """Court with no operating_hours → 24/7 — any time is valid."""
        court_no_hours = dict(_COURT_ROW, operating_hours=None)
        get_responses = [_mock_resp(200, [court_no_hours]), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", return_value=_slot_created_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": "2026-05-25T02:00:00Z",
                "end_at": "2026-05-25T04:00:00Z",
            })
        self.assertEqual(resp.status_code, 201)

    def test_slot_exactly_at_opening_time_is_valid(self):
        """start_at == open time → valid (boundary inclusive)."""
        # 08:00 on Monday (exactly at open)
        boundary_start = "2026-05-25T08:00:00Z"
        boundary_end = "2026-05-25T10:00:00Z"
        get_responses = [_court_resp(), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", return_value=_slot_created_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": boundary_start,
                "end_at": boundary_end,
            })
        self.assertEqual(resp.status_code, 201)

    def test_slot_exactly_at_closing_time_is_valid(self):
        """end_at == close time → valid (boundary inclusive)."""
        # 22:00 on Monday (exactly at close)
        boundary_start = "2026-05-25T20:00:00Z"
        boundary_end = "2026-05-25T22:00:00Z"
        get_responses = [_court_resp(), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", return_value=_slot_created_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": boundary_start,
                "end_at": boundary_end,
            })
        self.assertEqual(resp.status_code, 201)

    # ------------------------------------------------------------------
    # grava-3106.2.3 — Overlap detection (409 Slot conflict)
    # ------------------------------------------------------------------

    def test_overlapping_slot_returns_409(self):
        """Existing slot overlaps new slot → 409 Slot conflict."""
        get_responses = [_court_resp(), _overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 409)
        error = resp.json().get("error", "").lower()
        self.assertIn("conflict", error)

    def test_non_overlapping_slot_is_allowed(self):
        """No existing overlap → 201 created."""
        get_responses = [_court_resp(), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", return_value=_slot_created_resp()):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 201)

    def test_overlap_query_uses_correct_court_id(self):
        """Overlap check filters by court_id."""
        captured_calls = []

        def capture_get(url, params=None, **kwargs):
            captured_calls.append(params or {})
            if "courts" in url:
                return _court_resp()
            return _no_overlap_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=capture_get), \
             patch("courts.views.requests.post", return_value=_slot_created_resp()):
            self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })

        # Second GET call is the overlap check; it must filter by court_id
        self.assertGreaterEqual(len(captured_calls), 2)
        overlap_params = captured_calls[1]
        self.assertIn(f"eq.{_COURT_ID}", overlap_params.get("court_id", ""))

    # ------------------------------------------------------------------
    # grava-3106.2.4 — is_owner_slot → status=blocked
    # ------------------------------------------------------------------

    def test_is_owner_slot_true_forces_status_blocked(self):
        """is_owner_slot=true must set status=blocked regardless of provided status."""
        slot_row = dict(_SLOT_ROW, status="blocked", is_owner_slot=True)
        get_responses = [_court_resp(), _no_overlap_resp()]
        captured = {}

        def capture_post(*args, **kwargs):
            captured.update(kwargs)
            return _mock_resp(201, [slot_row])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", side_effect=capture_post):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
                "is_owner_slot": True,
            })
        self.assertEqual(resp.status_code, 201)
        sent = captured.get("json", {})
        self.assertEqual(sent.get("status"), "blocked")
        self.assertTrue(sent.get("is_owner_slot"))

    def test_is_owner_slot_true_ignores_provided_status(self):
        """is_owner_slot=true overrides any explicitly provided status to blocked."""
        slot_row = dict(_SLOT_ROW, status="blocked", is_owner_slot=True)
        get_responses = [_court_resp(), _no_overlap_resp()]
        captured = {}

        def capture_post(*args, **kwargs):
            captured.update(kwargs)
            return _mock_resp(201, [slot_row])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", side_effect=capture_post):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
                "is_owner_slot": True,
                "status": "open",  # Should be overridden
            })
        self.assertEqual(resp.status_code, 201)
        sent = captured.get("json", {})
        # Must be blocked regardless of what caller passed
        self.assertEqual(sent.get("status"), "blocked")

    def test_is_owner_slot_false_uses_provided_status(self):
        """is_owner_slot=false (default) uses the provided status."""
        slot_row = dict(_SLOT_ROW, status="maintenance", is_owner_slot=False)
        get_responses = [_court_resp(), _no_overlap_resp()]
        captured = {}

        def capture_post(*args, **kwargs):
            captured.update(kwargs)
            return _mock_resp(201, [slot_row])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", side_effect=capture_post):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
                "status": "maintenance",
                "is_owner_slot": False,
            })
        self.assertEqual(resp.status_code, 201)
        sent = captured.get("json", {})
        self.assertEqual(sent.get("status"), "maintenance")

    def test_is_owner_slot_defaults_to_false(self):
        """When is_owner_slot is omitted, defaults to False and status defaults to open."""
        get_responses = [_court_resp(), _no_overlap_resp()]
        captured = {}

        def capture_post(*args, **kwargs):
            captured.update(kwargs)
            return _slot_created_resp()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", side_effect=capture_post):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 201)
        sent = captured.get("json", {})
        self.assertFalse(sent.get("is_owner_slot"))
        self.assertEqual(sent.get("status"), "open")

    def test_response_shows_blocked_status_for_owner_slot(self):
        """Response for is_owner_slot=true has status=blocked."""
        slot_row = dict(_SLOT_ROW, status="blocked", is_owner_slot=True)
        get_responses = [_court_resp(), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", return_value=_mock_resp(201, [slot_row])):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
                "is_owner_slot": True,
            })
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body["status"], "blocked")
        self.assertTrue(body["is_owner_slot"])

    # ------------------------------------------------------------------
    # Error handling — service unavailable
    # ------------------------------------------------------------------

    def test_supabase_court_fetch_failure_returns_503(self):
        """Supabase courts endpoint down → 503."""
        import requests as req_lib

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 503)

    def test_supabase_slots_check_failure_returns_503(self):
        """Supabase slots overlap check fails → 503."""
        import requests as req_lib

        def side_effect(url, **kwargs):
            if "courts" in url:
                return _court_resp()
            raise req_lib.RequestException("timeout")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=side_effect):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 503)

    def test_supabase_slot_insert_failure_returns_503(self):
        """Supabase slot INSERT fails → 503."""
        import requests as req_lib

        get_responses = [_court_resp(), _no_overlap_resp()]
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_responses), \
             patch("courts.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self._post({
                "court_id": _COURT_ID,
                "start_at": _VALID_START,
                "end_at": _VALID_END,
            })
        self.assertEqual(resp.status_code, 503)

    # ------------------------------------------------------------------
    # HTTP method guard
    # ------------------------------------------------------------------

    def test_get_method_returns_405(self):
        """GET /api/courts/slots → 405 Method Not Allowed."""
        resp = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer token")
        self.assertEqual(resp.status_code, 405)
