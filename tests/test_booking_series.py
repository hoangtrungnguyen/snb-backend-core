"""
Tests for booking series endpoints (grava-3432.7 / BCORE-036).

Endpoints:
  POST /api/booking-series/preview  — preview occurrences without persisting
  POST /api/booking-series          — create booking series

Acceptance criteria:
  grava-3432.7.1 / BCORE-149: POST /booking-series/preview
    - body: {court_id, pattern, days_of_week, start_time, end_time, valid_from,
             end_condition: {type: 'after_n'|'until_date', value}}
  grava-3432.7.2 / BCORE-150: Returns generated occurrences without persisting:
    {occurrences: [{date, start_at, end_at, slot_id, conflict_reason}],
     total_sessions, total_hours, total_price, conflict_count}
  grava-3432.7.3 / BCORE-151: Conflict detection — occurrence conflicts if:
    - No matching open slot exists for that window, OR
    - Slot is already booked|blocked
  grava-3432.7.4 / BCORE-152: Auto-creates missing open slots within
    courts.operating_hours if no conflict — generated slots stay open until confirmed
  grava-3432.7.5 / BCORE-153: POST /booking-series
    - body: {...same pattern fields..., notes, skipped_dates: [DATE,...]}
  grava-3432.7.6 / BCORE-154: Transaction:
    - Insert booking_series row with status=pending
    - For each non-skipped occurrence: insert bookings row + lock/update slot to booked
    - If any slot lock fails, roll back entire series and return 409 SeriesConflictFailure
  grava-3432.7.7 / BCORE-155: Fixed-appointment series always start as pending
    — courts.auto_approve_single does NOT apply
  grava-3432.7.8 / BCORE-156: Owner receives one notification per series
  grava-3432.7.9 / BCORE-157: Player response includes series_id

All Supabase HTTP calls are mocked — no real network requests.
"""

import json
from unittest.mock import MagicMock, patch, call

import pytest
from django.test import Client, TestCase


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_PLAYER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OWNER_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_COURT_ID = "cccccccc-0000-0000-0000-000000000003"
_SLOT_ID_1 = "dddddddd-0000-0000-0000-000000000011"
_SLOT_ID_2 = "dddddddd-0000-0000-0000-000000000012"
_SLOT_ID_3 = "dddddddd-0000-0000-0000-000000000013"
_SERIES_ID = "eeeeeeee-0000-0000-0000-000000000005"
_BOOKING_ID_1 = "ffffffff-0000-0000-0000-000000000021"
_BOOKING_ID_2 = "ffffffff-0000-0000-0000-000000000022"
_BOOKING_ID_3 = "ffffffff-0000-0000-0000-000000000023"

_PLAYER_PAYLOAD = {
    "sub": _PLAYER_ID,
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OPERATING_HOURS = {
    "mon": {"open": "08:00", "close": "22:00"},
    "tue": {"open": "08:00", "close": "22:00"},
    "wed": {"open": "08:00", "close": "22:00"},
    "thu": {"open": "08:00", "close": "22:00"},
    "fri": {"open": "08:00", "close": "22:00"},
    "sat": {"open": "08:00", "close": "22:00"},
    "sun": {"open": "08:00", "close": "22:00"},
}

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Alpha",
    "price_per_hour": 100000,
    "auto_approve_single": True,  # should NOT apply to series
    "operating_hours": _OPERATING_HOURS,
}

# 2026-06-01 is a Monday; 2026-06-08 is the following Monday
# Use "weekly" pattern, mon+wed, start 2026-06-01, after_n=2 → 2 occurrences
# mon: 2026-06-01, wed: 2026-06-04

_PREVIEW_BODY = {
    "court_id": _COURT_ID,
    "pattern": "weekly",
    "days_of_week": ["mon", "wed"],
    "start_time": "09:00",
    "end_time": "11:00",
    "valid_from": "2026-06-01",
    "end_condition": {"type": "after_n", "value": 2},
}

_CREATE_BODY = {
    "court_id": _COURT_ID,
    "pattern": "weekly",
    "days_of_week": ["mon", "wed"],
    "start_time": "09:00",
    "end_time": "11:00",
    "valid_from": "2026-06-01",
    "end_condition": {"type": "after_n", "value": 2},
    "notes": "Weekly training session",
    "skipped_dates": [],
}

# Slot rows for the two occurrences (mon=2026-06-01, wed=2026-06-03)
# Note: 2026-06-01 is Monday, 2026-06-03 is Wednesday.
_SLOT_ROW_1 = {
    "id": _SLOT_ID_1,
    "court_id": _COURT_ID,
    "start_at": "2026-06-01T09:00:00+00:00",
    "end_at": "2026-06-01T11:00:00+00:00",
    "status": "open",
}
_SLOT_ROW_2 = {
    "id": _SLOT_ID_2,
    "court_id": _COURT_ID,
    "start_at": "2026-06-03T09:00:00+00:00",
    "end_at": "2026-06-03T11:00:00+00:00",
    "status": "open",
}

_SERIES_ROW = {
    "id": _SERIES_ID,
    "court_id": _COURT_ID,
    "user_id": _PLAYER_ID,
    "status": "pending",
    "pattern": "weekly",
    "days_of_week": ["mon", "wed"],
    "start_time": "09:00",
    "end_time": "11:00",
    "valid_from": "2026-06-01",
    "end_condition_type": "after_n",
    "end_condition_value": 2,
    "notes": "Weekly training session",
    "created_at": "2026-05-26T00:00:00+00:00",
    "updated_at": "2026-05-26T00:00:00+00:00",
}

_BOOKING_ROW_1 = {
    "id": _BOOKING_ID_1,
    "slot_id": _SLOT_ID_1,
    "user_id": _PLAYER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": _SERIES_ID,
    "status": "pending",
    "is_auto_approved": False,
    "is_walk_in": False,
    "notes": "Weekly training session",
    "created_at": "2026-05-26T00:00:00+00:00",
    "updated_at": "2026-05-26T00:00:00+00:00",
}

_BOOKING_ROW_2 = {
    "id": _BOOKING_ID_2,
    "slot_id": _SLOT_ID_2,
    "user_id": _PLAYER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": _SERIES_ID,
    "status": "pending",
    "is_auto_approved": False,
    "is_walk_in": False,
    "notes": "Weekly training session",
    "created_at": "2026-05-26T00:00:00+00:00",
    "updated_at": "2026-05-26T00:00:00+00:00",
}


def _mock_resp(status_code: int, data) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


def _player_token() -> str:
    return "valid.player.token"


def _patch_decode(payload):
    """Return a patcher that makes _decode_token return *payload*."""
    return patch(
        "auth_ext.middleware._decode_token",
        return_value=payload,
    )


# ---------------------------------------------------------------------------
# Preview endpoint tests
# ---------------------------------------------------------------------------


class TestBookingSeriesPreview(TestCase):
    """POST /api/booking-series/preview"""

    def setUp(self):
        self.client = Client()
        self.url = "/api/booking-series/preview"

    def _make_request(self, body, token="valid.player.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    # --- AC1: Auth required ---

    def test_preview_requires_auth(self):
        """401 when no Authorization header."""
        resp = self.client.post(
            self.url,
            data=json.dumps(_PREVIEW_BODY),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_preview_invalid_token_401(self):
        """401 when token is invalid."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._make_request(_PREVIEW_BODY, token="bad.token.here")
        self.assertEqual(resp.status_code, 401)

    # --- AC2: Validation ---

    def test_preview_missing_court_id_400(self):
        """400 when court_id is missing."""
        body = dict(_PREVIEW_BODY)
        del body["court_id"]
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertIn("court_id", data.get("error", "").lower())

    def test_preview_missing_pattern_400(self):
        """400 when pattern is missing."""
        body = dict(_PREVIEW_BODY)
        del body["pattern"]
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_invalid_pattern_400(self):
        """400 when pattern is not 'weekly'."""
        body = dict(_PREVIEW_BODY, pattern="daily")
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_missing_days_of_week_400(self):
        """400 when days_of_week is missing."""
        body = dict(_PREVIEW_BODY)
        del body["days_of_week"]
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_empty_days_of_week_400(self):
        """400 when days_of_week is empty."""
        body = dict(_PREVIEW_BODY, days_of_week=[])
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_invalid_day_key_400(self):
        """400 when days_of_week contains invalid day."""
        body = dict(_PREVIEW_BODY, days_of_week=["mon", "xyz"])
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_missing_start_time_400(self):
        """400 when start_time is missing."""
        body = dict(_PREVIEW_BODY)
        del body["start_time"]
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_invalid_start_time_400(self):
        """400 when start_time format is invalid."""
        body = dict(_PREVIEW_BODY, start_time="9am")
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_end_time_before_start_400(self):
        """400 when end_time <= start_time."""
        body = dict(_PREVIEW_BODY, start_time="11:00", end_time="09:00")
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_missing_valid_from_400(self):
        """400 when valid_from is missing."""
        body = dict(_PREVIEW_BODY)
        del body["valid_from"]
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_invalid_valid_from_400(self):
        """400 when valid_from format is invalid."""
        body = dict(_PREVIEW_BODY, valid_from="01-06-2026")
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_missing_end_condition_400(self):
        """400 when end_condition is missing."""
        body = dict(_PREVIEW_BODY)
        del body["end_condition"]
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_invalid_end_condition_type_400(self):
        """400 when end_condition.type is invalid."""
        body = dict(_PREVIEW_BODY, end_condition={"type": "forever", "value": 5})
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_after_n_zero_400(self):
        """400 when after_n value is 0."""
        body = dict(_PREVIEW_BODY, end_condition={"type": "after_n", "value": 0})
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_court_not_found_404(self):
        """404 when court does not exist."""
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.return_value = _mock_resp(200, [])  # court not found
                resp = self._make_request(_PREVIEW_BODY)
        self.assertEqual(resp.status_code, 404)

    # --- AC3: Happy path — after_n end condition ---

    def test_preview_after_n_no_conflicts(self):
        """
        Preview with after_n=2, mon+wed, all slots open.
        Expects 2 occurrences, no conflicts, correct totals.
        """
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
                # Court lookup
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),    # court
                    _mock_resp(200, [_SLOT_ROW_1]),   # slot lookup for 2026-06-01 09:00-11:00
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot lookup for 2026-06-04 09:00-11:00
                ]
                resp = self._make_request(_PREVIEW_BODY)

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("occurrences", data)
        self.assertIn("total_sessions", data)
        self.assertIn("total_hours", data)
        self.assertIn("total_price", data)
        self.assertIn("conflict_count", data)

        self.assertEqual(data["total_sessions"], 2)
        self.assertEqual(data["conflict_count"], 0)
        self.assertEqual(len(data["occurrences"]), 2)

        occ1, occ2 = data["occurrences"]
        self.assertEqual(occ1["date"], "2026-06-01")
        self.assertIsNone(occ1["conflict_reason"])
        self.assertEqual(occ1["slot_id"], _SLOT_ID_1)

        self.assertEqual(occ2["date"], "2026-06-03")
        self.assertIsNone(occ2["conflict_reason"])
        self.assertEqual(occ2["slot_id"], _SLOT_ID_2)

        # 2 sessions × 2h = 4 hours
        self.assertAlmostEqual(data["total_hours"], 4.0)
        # price = 4h × 100000
        self.assertAlmostEqual(data["total_price"], 400000.0)

    def test_preview_after_n_with_conflict(self):
        """
        Preview where one slot is booked → conflict_count=1, conflict_reason set.
        """
        booked_slot = dict(_SLOT_ROW_1, status="booked")
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),    # court
                    _mock_resp(200, [booked_slot]),   # slot 1: booked → conflict
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot 2: open → ok
                ]
                resp = self._make_request(_PREVIEW_BODY)

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["conflict_count"], 1)
        self.assertIsNotNone(data["occurrences"][0]["conflict_reason"])
        self.assertIsNone(data["occurrences"][1]["conflict_reason"])

    def test_preview_slot_missing_is_conflict(self):
        """
        Preview where no slot exists for the window → conflict (no open slot).
        """
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),  # court
                    _mock_resp(200, []),              # slot 1: no slot → conflict
                    _mock_resp(200, [_SLOT_ROW_2]),  # slot 2: open → ok
                ]
                resp = self._make_request(_PREVIEW_BODY)

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["conflict_count"], 1)
        self.assertIsNotNone(data["occurrences"][0]["conflict_reason"])

    # --- AC4: until_date end condition ---

    def test_preview_until_date_end_condition(self):
        """
        Preview with end_condition type=until_date.
        valid_from=2026-06-01, until_date=2026-06-07, days=[mon, wed]
        2026-06-01 is Monday, 2026-06-03 is Wednesday (both within range).
        """
        body = dict(_PREVIEW_BODY, end_condition={"type": "until_date", "value": "2026-06-07"})
        # days_of_week = ["mon", "wed"]: mon=2026-06-01, wed=2026-06-03 (both within range)
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_SLOT_ROW_1]),  # mon 2026-06-01
                    _mock_resp(200, [_SLOT_ROW_2]),  # wed 2026-06-03
                ]
                resp = self._make_request(body)

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["total_sessions"], 2)

    def test_preview_invalid_until_date_400(self):
        """400 when until_date value is not YYYY-MM-DD."""
        body = dict(_PREVIEW_BODY, end_condition={"type": "until_date", "value": "not-a-date"})
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    # --- AC5: Response shape correctness ---

    def test_preview_occurrence_shape(self):
        """Each occurrence has: date, start_at, end_at, slot_id, conflict_reason."""
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_SLOT_ROW_1]),
                    _mock_resp(200, [_SLOT_ROW_2]),
                ]
                resp = self._make_request(_PREVIEW_BODY)

        data = json.loads(resp.content)
        occ = data["occurrences"][0]
        self.assertIn("date", occ)
        self.assertIn("start_at", occ)
        self.assertIn("end_at", occ)
        self.assertIn("slot_id", occ)
        self.assertIn("conflict_reason", occ)

    # --- AC6: Max session cap ---

    def test_preview_after_n_max_cap(self):
        """after_n > 52 (max 1 year weekly) returns 400."""
        body = dict(_PREVIEW_BODY, end_condition={"type": "after_n", "value": 200})
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_preview_until_date_too_far_400(self):
        """400 when until_date is more than 365 days from valid_from."""
        body = dict(
            _PREVIEW_BODY,
            end_condition={"type": "until_date", "value": "2030-01-01"},
        )
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    # --- Outside operating hours ---
    def test_preview_slot_outside_operating_hours_is_conflict(self):
        """Occurrence time outside operating_hours is a conflict."""
        limited_hours = {
            "mon": {"open": "10:00", "close": "22:00"},  # 09:00 is before open
            "wed": {"open": "08:00", "close": "22:00"},
        }
        court_limited = dict(_COURT_ROW, operating_hours=limited_hours)
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [court_limited]),
                    _mock_resp(200, [_SLOT_ROW_2]),  # wed ok
                ]
                resp = self._make_request(_PREVIEW_BODY)

        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        # mon is outside hours
        self.assertEqual(data["conflict_count"], 1)
        # Find the mon occurrence
        mon_occ = next(o for o in data["occurrences"] if o["date"] == "2026-06-01")
        self.assertIsNotNone(mon_occ["conflict_reason"])


# ---------------------------------------------------------------------------
# Create endpoint tests
# ---------------------------------------------------------------------------


class TestBookingSeriesCreate(TestCase):
    """POST /api/booking-series"""

    def setUp(self):
        self.client = Client()
        self.url = "/api/booking-series"

    def _make_request(self, body, token="valid.player.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    # --- AC1: Auth required ---

    def test_create_requires_auth(self):
        """401 when no Authorization header."""
        resp = self.client.post(
            self.url,
            data=json.dumps(_CREATE_BODY),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    # --- AC2: Validation ---

    def test_create_missing_court_id_400(self):
        """400 when court_id is missing."""
        body = dict(_CREATE_BODY)
        del body["court_id"]
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self._make_request(body)
        self.assertEqual(resp.status_code, 400)

    def test_create_court_not_found_404(self):
        """404 when court doesn't exist."""
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.return_value = _mock_resp(200, [])
                resp = self._make_request(_CREATE_BODY)
        self.assertEqual(resp.status_code, 404)

    # --- AC3 (grava-3432.7.7): Series always pending, auto_approve_single ignored ---

    def test_create_series_always_pending(self):
        """
        Even when court.auto_approve_single=True, series status=pending.
        grava-3432.7.7: Fixed-appointment series always start as pending.
        """
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post, \
                 patch("requests.patch") as mock_patch:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),    # court (auto_approve_single=True)
                    _mock_resp(200, [_SLOT_ROW_1]),   # slot 1 resolution
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot 2 resolution
                    _mock_resp(200, [_SLOT_ROW_1]),   # slot 1 lock check
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot 2 lock check
                ]
                # booking_series insert, then 2 booking inserts, then notification
                mock_post.side_effect = [
                    _mock_resp(201, [_SERIES_ROW]),
                    _mock_resp(201, [_BOOKING_ROW_1]),
                    _mock_resp(201, [_BOOKING_ROW_2]),
                    _mock_resp(201, {}),  # notification
                ]
                mock_patch.return_value = _mock_resp(200, [])  # slot update
                resp = self._make_request(_CREATE_BODY)

        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertEqual(data["status"], "pending")

    # --- AC4: Success path ---

    def test_create_series_happy_path(self):
        """
        Successful series creation:
        - 201 response
        - series_id in response (grava-3432.7.9)
        - bookings created for each occurrence
        - slots marked booked
        """
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post, \
                 patch("requests.patch") as mock_patch:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_SLOT_ROW_1]),   # slot 1 resolution
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot 2 resolution
                    _mock_resp(200, [_SLOT_ROW_1]),   # slot 1 lock check
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot 2 lock check
                ]
                mock_post.side_effect = [
                    _mock_resp(201, [_SERIES_ROW]),
                    _mock_resp(201, [_BOOKING_ROW_1]),
                    _mock_resp(201, [_BOOKING_ROW_2]),
                    _mock_resp(201, {}),  # notification
                ]
                mock_patch.return_value = _mock_resp(200, [])
                resp = self._make_request(_CREATE_BODY)

        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)

        # grava-3432.7.9: response includes series_id
        self.assertIn("series_id", data)
        self.assertEqual(data["series_id"], _SERIES_ID)

        # grava-3432.7.6: booking_series row has status=pending
        self.assertEqual(data["status"], "pending")

        # grava-3432.7.6: number of bookings created
        self.assertIn("bookings_created", data)
        self.assertEqual(data["bookings_created"], 2)

    # --- AC5 (grava-3432.7.6): Transaction — slot conflict rolls back entire series ---

    def test_create_series_slot_conflict_409(self):
        """
        If a slot becomes unavailable between resolution and lock-check,
        return 409 SeriesConflictFailure and roll back (delete) the booking_series row.
        grava-3432.7.6: roll back entire series on conflict (race condition).
        """
        # Slot 1 appears open during resolution but becomes "booked" at lock-check time
        booked_slot_1_locked = dict(_SLOT_ROW_1, status="booked")
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.delete") as mock_delete:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_SLOT_ROW_1]),         # slot 1 resolution: appears open
                    _mock_resp(200, [_SLOT_ROW_2]),         # slot 2 resolution: open
                    _mock_resp(200, [booked_slot_1_locked]),  # slot 1 lock check: now booked!
                ]
                mock_post.side_effect = [
                    _mock_resp(201, [_SERIES_ROW]),    # series created
                    # No bookings created — conflict stops at lock-check
                ]
                mock_delete.return_value = _mock_resp(204, {})  # rollback delete
                resp = self._make_request(_CREATE_BODY)

        self.assertEqual(resp.status_code, 409)
        data = json.loads(resp.content)
        self.assertIn("SeriesConflictFailure", data.get("error", ""))
        # Rollback: series row deleted
        mock_delete.assert_called()

    # --- AC6 (grava-3432.7.5): skipped_dates excludes occurrences ---

    def test_create_series_skipped_dates(self):
        """
        When skipped_dates includes an occurrence date, that occurrence is excluded.
        """
        body = dict(_CREATE_BODY, skipped_dates=["2026-06-01"])  # skip first mon (2026-06-01)
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post, \
                 patch("requests.patch") as mock_patch:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_SLOT_ROW_2]),  # only wed slot resolution
                    _mock_resp(200, [_SLOT_ROW_2]),  # wed slot lock check
                ]
                mock_post.side_effect = [
                    _mock_resp(201, [_SERIES_ROW]),
                    _mock_resp(201, [_BOOKING_ROW_2]),  # only 1 booking
                    _mock_resp(201, {}),  # notification
                ]
                mock_patch.return_value = _mock_resp(200, [])
                resp = self._make_request(body)

        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertEqual(data["bookings_created"], 1)

    # --- AC7 (grava-3432.7.4): Auto-create missing slots ---

    def test_create_series_auto_creates_missing_slot(self):
        """
        If no slot exists for an occurrence window, auto-create it within operating_hours.
        grava-3432.7.4: generated slots stay open until series is confirmed.
        """
        new_slot = {
            "id": "newslot-0000-0000-0000-000000000099",
            "court_id": _COURT_ID,
            "start_at": "2026-06-01T09:00:00+00:00",
            "end_at": "2026-06-01T11:00:00+00:00",
            "status": "open",
        }
        booking_for_new = dict(_BOOKING_ROW_1, slot_id=new_slot["id"])
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post, \
                 patch("requests.patch") as mock_patch:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, []),              # no existing slot for mon — triggers auto-create
                    _mock_resp(200, [_SLOT_ROW_2]),  # wed slot exists
                    _mock_resp(200, [new_slot]),      # slot 1 (auto-created) lock check: still open
                    _mock_resp(200, [_SLOT_ROW_2]),  # slot 2 lock check: still open
                ]
                mock_post.side_effect = [
                    _mock_resp(201, [new_slot]),          # auto-created slot
                    _mock_resp(201, [_SERIES_ROW]),       # series
                    _mock_resp(201, [booking_for_new]),   # booking 1
                    _mock_resp(201, [_BOOKING_ROW_2]),    # booking 2
                    _mock_resp(201, {}),                  # notification
                ]
                mock_patch.return_value = _mock_resp(200, [])
                resp = self._make_request(_CREATE_BODY)

        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertEqual(data["bookings_created"], 2)

    # --- AC8 (grava-3432.7.8): Owner notification ---

    def test_create_series_sends_owner_notification(self):
        """
        Owner receives exactly one notification per series.
        grava-3432.7.8
        """
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post, \
                 patch("requests.patch") as mock_patch:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_SLOT_ROW_1]),   # slot 1 resolution
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot 2 resolution
                    _mock_resp(200, [_SLOT_ROW_1]),   # slot 1 lock check
                    _mock_resp(200, [_SLOT_ROW_2]),   # slot 2 lock check
                ]
                mock_post.side_effect = [
                    _mock_resp(201, [_SERIES_ROW]),
                    _mock_resp(201, [_BOOKING_ROW_1]),
                    _mock_resp(201, [_BOOKING_ROW_2]),
                    _mock_resp(201, {}),  # notification
                ]
                mock_patch.return_value = _mock_resp(200, [])
                resp = self._make_request(_CREATE_BODY)

        self.assertEqual(resp.status_code, 201)
        # Check notification was sent — the POST to notifications endpoint
        notification_post_calls = [
            c for c in mock_post.call_args_list
            if "notifications" in str(c)
        ]
        self.assertEqual(len(notification_post_calls), 1)
        # The notification must carry owner_id as user_id
        notif_payload = notification_post_calls[0].kwargs.get("json") or {}
        self.assertEqual(notif_payload.get("user_id"), _OWNER_ID)

    # --- AC9: Service unavailable ---

    def test_create_court_service_unavailable_503(self):
        """503 when Supabase court lookup fails."""
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.return_value = _mock_resp(500, {})
                resp = self._make_request(_CREATE_BODY)
        self.assertEqual(resp.status_code, 503)

    # --- AC10: All occurrences skipped → no bookings created ---

    def test_create_series_all_skipped_201(self):
        """
        When all occurrences are in skipped_dates, creates series with 0 bookings.
        """
        body = dict(_CREATE_BODY, skipped_dates=["2026-06-01", "2026-06-03"])
        with _patch_decode(_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, patch("requests.post") as mock_post, \
                 patch("requests.patch") as mock_patch:
                mock_get.side_effect = [
                    _mock_resp(200, [_COURT_ROW]),
                ]
                mock_post.side_effect = [
                    _mock_resp(201, [_SERIES_ROW]),
                    _mock_resp(201, {}),  # notification (still sent)
                ]
                mock_patch.return_value = _mock_resp(200, [])
                resp = self._make_request(body)

        self.assertEqual(resp.status_code, 201)
        data = json.loads(resp.content)
        self.assertEqual(data["bookings_created"], 0)

    # --- Method not allowed ---

    def test_get_not_allowed(self):
        """GET on /api/booking-series returns 405."""
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self.client.get(
                self.url,
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(resp.status_code, 405)

    def test_preview_get_not_allowed(self):
        """GET on /api/booking-series/preview returns 405."""
        with _patch_decode(_PLAYER_PAYLOAD):
            resp = self.client.get(
                "/api/booking-series/preview",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(resp.status_code, 405)
