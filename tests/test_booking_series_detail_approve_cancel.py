"""
Tests for booking series detail, approve & cancel (grava-3432.8 / BCORE-037).

Endpoints:
  GET   /api/booking-series/<id>          — series detail with occurrences
  PATCH /api/booking-series/<id>/status   — approve (pending→confirmed) or cancel

Acceptance Criteria (from spec):
  Response shape for GET:
    {id, court_id, court_name, pattern, days_of_week, start_time, end_time,
     valid_from, valid_until, status, total_sessions, sessions_played,
     sessions_upcoming, sessions_cancelled,
     occurrences: [{booking_id, slot_id, date, start_at, end_at, status}]}

  PATCH status:
    - pending → confirmed: court owner only (OWNER-27)
    - pending → cancelled / confirmed → cancelled: owner or series player (CAPP-056)
    - On approve: all pending bookings in series → confirmed; slots stay booked;
      player notification sent
    - On cancel: all pending/confirmed bookings → cancelled; slots → open;
      owner notification sent (player-initiated cancel) or player notification (owner cancel)
    - Auth: 401 when no/invalid token
    - Series not found: 404
    - Service unavailable: 503

Surfaces: CAPP-055, CAPP-056, OWNER-27.

All Supabase HTTP calls are mocked — no real network requests.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_PLAYER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OWNER_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_OTHER_PLAYER_ID = "cccccccc-0000-0000-0000-000000000099"
_COURT_ID = "cccccccc-0000-0000-0000-000000000003"
_SLOT_ID_1 = "dddddddd-0000-0000-0000-000000000011"
_SLOT_ID_2 = "dddddddd-0000-0000-0000-000000000012"
_SERIES_ID = "eeeeeeee-0000-0000-0000-000000000005"
_BOOKING_ID_1 = "ffffffff-0000-0000-0000-000000000021"
_BOOKING_ID_2 = "ffffffff-0000-0000-0000-000000000022"

_PLAYER_PAYLOAD = {
    "sub": _PLAYER_ID,
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OWNER_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_OTHER_PLAYER_PAYLOAD = {
    "sub": _OTHER_PLAYER_ID,
    "email": "other@example.com",
    "app_metadata": {"role": "player"},
}

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Alpha",
    "price_per_hour": 100000,
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
    "end_condition_value": "2",
    "notes": "Weekly training",
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
    "created_at": "2026-05-26T00:00:00+00:00",
    "updated_at": "2026-05-26T00:00:00+00:00",
}

_SLOT_ROW_1 = {
    "id": _SLOT_ID_1,
    "court_id": _COURT_ID,
    "start_at": "2026-06-01T09:00:00+00:00",
    "end_at": "2026-06-01T11:00:00+00:00",
    "status": "booked",
}

_SLOT_ROW_2 = {
    "id": _SLOT_ID_2,
    "court_id": _COURT_ID,
    "start_at": "2026-06-03T09:00:00+00:00",
    "end_at": "2026-06-03T11:00:00+00:00",
    "status": "booked",
}


def _mock_resp(status_code: int, data) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    return resp


_DETAIL_URL = f"/api/booking-series/{_SERIES_ID}"
_STATUS_URL = f"/api/booking-series/{_SERIES_ID}/status"


# ---------------------------------------------------------------------------
# GET /api/booking-series/<id>  — Series detail
# ---------------------------------------------------------------------------


class TestBookingSeriesDetail(TestCase):
    """GET /api/booking-series/<id>"""

    def setUp(self):
        self.client = Client()

    # --- Auth ---

    def test_detail_requires_auth_401(self):
        """401 when no Authorization header."""
        resp = self.client.get(_DETAIL_URL)
        self.assertEqual(resp.status_code, 401)

    def test_detail_invalid_token_401(self):
        """401 when token is invalid."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self.client.get(
                _DETAIL_URL, HTTP_AUTHORIZATION="Bearer bad.token"
            )
        self.assertEqual(resp.status_code, 401)

    # --- Not found ---

    def test_detail_series_not_found_404(self):
        """404 when series does not exist."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.return_value = _mock_resp(200, [])  # series not found
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )
        self.assertEqual(resp.status_code, 404)

    # --- Access control ---

    def test_detail_player_can_view_own_series(self):
        """Player can view their own series."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),            # series
                    _mock_resp(200, [_COURT_ROW]),             # court
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),  # bookings
                    _mock_resp(200, [_SLOT_ROW_1]),            # slot for booking 1
                    _mock_resp(200, [_SLOT_ROW_2]),            # slot for booking 2
                ]
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )
        self.assertEqual(resp.status_code, 200)

    def test_detail_other_player_forbidden_403(self):
        """Another player cannot view a series they don't own."""
        other_series = dict(_SERIES_ROW, user_id=_PLAYER_ID)  # owned by player, not other
        with patch("auth_ext.middleware._decode_token", return_value=_OTHER_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [other_series]),
                    _mock_resp(200, [_COURT_ROW]),  # court owned by _OWNER_ID
                ]
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )
        self.assertEqual(resp.status_code, 403)

    def test_detail_owner_can_view_series_for_their_court(self):
        """Court owner can view any series for their court."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),              # owner_id matches
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                    _mock_resp(200, [_SLOT_ROW_1]),
                    _mock_resp(200, [_SLOT_ROW_2]),
                ]
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )
        self.assertEqual(resp.status_code, 200)

    # --- Response shape (CAPP-055) ---

    def test_detail_response_shape(self):
        """
        Response includes all required fields:
        id, court_id, court_name, pattern, days_of_week, start_time, end_time,
        valid_from, valid_until, status, total_sessions, sessions_played,
        sessions_upcoming, sessions_cancelled, occurrences
        """
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                    _mock_resp(200, [_SLOT_ROW_1]),
                    _mock_resp(200, [_SLOT_ROW_2]),
                ]
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        required_fields = [
            "id", "court_id", "court_name", "pattern", "days_of_week",
            "start_time", "end_time", "valid_from", "valid_until", "status",
            "total_sessions", "sessions_played", "sessions_upcoming",
            "sessions_cancelled", "occurrences",
        ]
        for field in required_fields:
            self.assertIn(field, data, f"Missing field: {field}")

        self.assertEqual(data["id"], _SERIES_ID)
        self.assertEqual(data["court_id"], _COURT_ID)
        self.assertEqual(data["court_name"], "Court Alpha")
        self.assertEqual(data["status"], "pending")

    def test_detail_occurrences_shape(self):
        """Each occurrence has: booking_id, slot_id, date, start_at, end_at, status."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                    _mock_resp(200, [_SLOT_ROW_1]),
                    _mock_resp(200, [_SLOT_ROW_2]),
                ]
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )

        data = resp.json()
        self.assertEqual(len(data["occurrences"]), 2)
        occ = data["occurrences"][0]
        for field in ["booking_id", "slot_id", "date", "start_at", "end_at", "status"]:
            self.assertIn(field, occ, f"Occurrence missing field: {field}")

    def test_detail_session_counts(self):
        """
        total_sessions = 2, sessions_played = 0, sessions_upcoming = 2, sessions_cancelled = 0
        (both bookings are pending)
        """
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                    _mock_resp(200, [_SLOT_ROW_1]),
                    _mock_resp(200, [_SLOT_ROW_2]),
                ]
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )

        data = resp.json()
        self.assertEqual(data["total_sessions"], 2)
        self.assertEqual(data["sessions_played"], 0)
        self.assertEqual(data["sessions_cancelled"], 0)
        # sessions_upcoming = pending + confirmed
        self.assertEqual(data["sessions_upcoming"], 2)

    def test_detail_session_counts_with_mixed_statuses(self):
        """
        When bookings have mixed statuses, session counts are correct:
        booking1=confirmed, booking2=cancelled → played=1, cancelled=1, upcoming=1
        """
        confirmed_booking = dict(_BOOKING_ROW_1, status="confirmed")
        cancelled_booking = dict(_BOOKING_ROW_2, status="cancelled")

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [confirmed_booking, cancelled_booking]),
                    _mock_resp(200, [_SLOT_ROW_1]),
                    _mock_resp(200, [_SLOT_ROW_2]),
                ]
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )

        data = resp.json()
        self.assertEqual(data["total_sessions"], 2)
        self.assertEqual(data["sessions_played"], 1)   # confirmed
        self.assertEqual(data["sessions_cancelled"], 1)  # cancelled
        self.assertEqual(data["sessions_upcoming"], 1)   # confirmed = upcoming (not yet completed)

    def test_detail_service_unavailable_503(self):
        """503 when Supabase series lookup fails."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.return_value = _mock_resp(500, {})
                resp = self.client.get(
                    _DETAIL_URL, HTTP_AUTHORIZATION="Bearer valid.token"
                )
        self.assertEqual(resp.status_code, 503)

    def test_detail_method_not_allowed_post_405(self):
        """POST on detail endpoint → 405."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.post(
                _DETAIL_URL,
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# PATCH /api/booking-series/<id>/status  — Approve / Cancel
# ---------------------------------------------------------------------------


class TestBookingSeriesApprove(TestCase):
    """OWNER-27: PATCH /api/booking-series/<id>/status {status: confirmed}"""

    def setUp(self):
        self.client = Client()

    def _patch(self, body, payload=_OWNER_PAYLOAD):
        with patch("auth_ext.middleware._decode_token", return_value=payload):
            return self.client.patch(
                _STATUS_URL,
                data=json.dumps(body),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )

    def test_owner_approves_pending_series_200(self):
        """
        OWNER-27: Owner approves pending series → all bookings → confirmed,
        series status → confirmed. 200 response.
        """
        confirmed_series = dict(_SERIES_ROW, status="confirmed")
        confirmed_b1 = dict(_BOOKING_ROW_1, status="confirmed")
        confirmed_b2 = dict(_BOOKING_ROW_2, status="confirmed")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.post") as mock_post:

                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),            # series
                    _mock_resp(200, [_COURT_ROW]),             # court
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),  # bookings
                ]
                mock_patch.side_effect = [
                    _mock_resp(200, [confirmed_b1]),   # booking 1 → confirmed
                    _mock_resp(200, [confirmed_b2]),   # booking 2 → confirmed
                    _mock_resp(200, [confirmed_series]),  # series → confirmed
                ]
                mock_post.return_value = _mock_resp(201, {})  # notification

                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "confirmed")

    def test_approve_sends_notification_to_player(self):
        """On approve, player receives notification."""
        confirmed_series = dict(_SERIES_ROW, status="confirmed")
        confirmed_b1 = dict(_BOOKING_ROW_1, status="confirmed")
        confirmed_b2 = dict(_BOOKING_ROW_2, status="confirmed")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.post") as mock_post:

                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                ]
                mock_patch.side_effect = [
                    _mock_resp(200, [confirmed_b1]),
                    _mock_resp(200, [confirmed_b2]),
                    _mock_resp(200, [confirmed_series]),
                ]
                mock_post.return_value = _mock_resp(201, {})

                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )

        self.assertEqual(resp.status_code, 200)
        notif_calls = [c for c in mock_post.call_args_list if "notifications" in str(c)]
        self.assertGreaterEqual(len(notif_calls), 1)
        notif_body = notif_calls[0].kwargs.get("json", {})
        self.assertEqual(notif_body.get("user_id"), _PLAYER_ID)

    def test_player_cannot_approve_series_403(self):
        """Player cannot approve a series → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                ]
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 403)

    def test_other_owner_cannot_approve_403(self):
        """An owner who doesn't own this court cannot approve → 403."""
        other_owner_payload = {
            "sub": "zzzzzzzz-0000-0000-0000-000000000099",
            "email": "othercourt@example.com",
            "app_metadata": {"role": "owner"},
        }
        with patch("auth_ext.middleware._decode_token", return_value=other_owner_payload):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),  # court.owner_id = _OWNER_ID, not other
                ]
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 403)

    def test_approve_already_confirmed_409(self):
        """Approving an already confirmed series → 409."""
        confirmed_series = dict(_SERIES_ROW, status="confirmed")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [confirmed_series]),
                    _mock_resp(200, [_COURT_ROW]),
                ]
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 409)

    def test_approve_cancelled_series_409(self):
        """Cannot approve a cancelled series → 409."""
        cancelled_series = dict(_SERIES_ROW, status="cancelled")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [cancelled_series]),
                    _mock_resp(200, [_COURT_ROW]),
                ]
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 409)

    def test_approve_series_not_found_404(self):
        """404 when series does not exist."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.return_value = _mock_resp(200, [])
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 404)

    def test_approve_requires_auth_401(self):
        """401 when no Authorization header."""
        resp = self.client.patch(
            _STATUS_URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_approve_missing_status_400(self):
        """400 when status field is missing."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.patch(
                _STATUS_URL,
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(resp.status_code, 400)

    def test_approve_invalid_status_400(self):
        """400 when status value is not allowed."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.patch(
                _STATUS_URL,
                data=json.dumps({"status": "completed"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(resp.status_code, 400)

    def test_approve_invalid_json_400(self):
        """400 when request body is invalid JSON."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.patch(
                _STATUS_URL,
                data="not-json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(resp.status_code, 400)

    def test_approve_service_unavailable_503(self):
        """503 when Supabase series lookup fails."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.return_value = _mock_resp(500, {})
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "confirmed"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 503)


class TestBookingSeriesCancel(TestCase):
    """CAPP-056 / OWNER-27: PATCH /api/booking-series/<id>/status {status: cancelled}"""

    def setUp(self):
        self.client = Client()

    def test_owner_cancels_pending_series_200(self):
        """
        OWNER-27: Owner cancels pending series → all bookings → cancelled,
        slots → open, series status → cancelled.
        """
        cancelled_series = dict(_SERIES_ROW, status="cancelled")
        cancelled_b1 = dict(_BOOKING_ROW_1, status="cancelled")
        cancelled_b2 = dict(_BOOKING_ROW_2, status="cancelled")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.post") as mock_post:

                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                ]
                # booking1→cancelled, slot1→open, booking2→cancelled, slot2→open, series→cancelled
                mock_patch.side_effect = [
                    _mock_resp(200, [cancelled_b1]),
                    _mock_resp(200, []),  # slot 1 → open
                    _mock_resp(200, [cancelled_b2]),
                    _mock_resp(200, []),  # slot 2 → open
                    _mock_resp(200, [cancelled_series]),  # series → cancelled
                ]
                mock_post.return_value = _mock_resp(201, {})

                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "cancelled"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "cancelled")

    def test_owner_cancel_restores_slots_to_open(self):
        """On series cancel, all linked slots are restored to 'open'."""
        cancelled_series = dict(_SERIES_ROW, status="cancelled")
        cancelled_b1 = dict(_BOOKING_ROW_1, status="cancelled")
        cancelled_b2 = dict(_BOOKING_ROW_2, status="cancelled")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.post") as mock_post:

                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                ]
                mock_patch.side_effect = [
                    _mock_resp(200, [cancelled_b1]),
                    _mock_resp(200, []),  # slot 1 → open
                    _mock_resp(200, [cancelled_b2]),
                    _mock_resp(200, []),  # slot 2 → open
                    _mock_resp(200, [cancelled_series]),
                ]
                mock_post.return_value = _mock_resp(201, {})

                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "cancelled"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )

        self.assertEqual(resp.status_code, 200)
        # Verify slots were patched to "open"
        slot_patch_calls = [
            c for c in mock_patch.call_args_list if "slots" in str(c)
        ]
        self.assertGreaterEqual(len(slot_patch_calls), 2)
        for call in slot_patch_calls:
            body = call.kwargs.get("json", {})
            self.assertEqual(body.get("status"), "open")

    def test_player_cancels_own_series_200(self):
        """
        CAPP-056: Series creator (player) can cancel their own series.
        """
        cancelled_series = dict(_SERIES_ROW, status="cancelled")
        cancelled_b1 = dict(_BOOKING_ROW_1, status="cancelled")
        cancelled_b2 = dict(_BOOKING_ROW_2, status="cancelled")

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.post") as mock_post:

                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                ]
                mock_patch.side_effect = [
                    _mock_resp(200, [cancelled_b1]),
                    _mock_resp(200, []),  # slot 1 → open
                    _mock_resp(200, [cancelled_b2]),
                    _mock_resp(200, []),  # slot 2 → open
                    _mock_resp(200, [cancelled_series]),
                ]
                mock_post.return_value = _mock_resp(201, {})

                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "cancelled"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )

        self.assertEqual(resp.status_code, 200)

    def test_other_player_cannot_cancel_series_403(self):
        """Another player who is not the series creator cannot cancel → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_OTHER_PLAYER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),  # series.user_id = _PLAYER_ID
                    _mock_resp(200, [_COURT_ROW]),   # court.owner_id = _OWNER_ID
                ]
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "cancelled"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 403)

    def test_cancel_already_cancelled_series_409(self):
        """Cancelling an already cancelled series → 409."""
        cancelled_series = dict(_SERIES_ROW, status="cancelled")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get:
                mock_get.side_effect = [
                    _mock_resp(200, [cancelled_series]),
                    _mock_resp(200, [_COURT_ROW]),
                ]
                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "cancelled"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )
        self.assertEqual(resp.status_code, 409)

    def test_cancel_only_cancels_active_bookings(self):
        """
        On series cancel, only pending/confirmed bookings are cancelled;
        already-cancelled bookings are skipped.
        """
        already_cancelled = dict(_BOOKING_ROW_2, status="cancelled")
        cancelled_series = dict(_SERIES_ROW, status="cancelled")
        cancelled_b1 = dict(_BOOKING_ROW_1, status="cancelled")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.post") as mock_post:

                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, already_cancelled]),
                ]
                # Only 1 active booking to cancel + slot + series
                mock_patch.side_effect = [
                    _mock_resp(200, [cancelled_b1]),
                    _mock_resp(200, []),  # slot 1 → open
                    _mock_resp(200, [cancelled_series]),
                ]
                mock_post.return_value = _mock_resp(201, {})

                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "cancelled"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )

        self.assertEqual(resp.status_code, 200)
        # Only 1 booking was patched (the other was already cancelled)
        booking_patch_calls = [
            c for c in mock_patch.call_args_list if "bookings" in str(c)
        ]
        self.assertEqual(len(booking_patch_calls), 1)

    def test_cancel_sends_notification(self):
        """A notification is sent when series is cancelled."""
        cancelled_series = dict(_SERIES_ROW, status="cancelled")
        cancelled_b1 = dict(_BOOKING_ROW_1, status="cancelled")
        cancelled_b2 = dict(_BOOKING_ROW_2, status="cancelled")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            with patch("requests.get") as mock_get, \
                 patch("requests.patch") as mock_patch, \
                 patch("requests.post") as mock_post:

                mock_get.side_effect = [
                    _mock_resp(200, [_SERIES_ROW]),
                    _mock_resp(200, [_COURT_ROW]),
                    _mock_resp(200, [_BOOKING_ROW_1, _BOOKING_ROW_2]),
                ]
                mock_patch.side_effect = [
                    _mock_resp(200, [cancelled_b1]),
                    _mock_resp(200, []),
                    _mock_resp(200, [cancelled_b2]),
                    _mock_resp(200, []),
                    _mock_resp(200, [cancelled_series]),
                ]
                mock_post.return_value = _mock_resp(201, {})

                resp = self.client.patch(
                    _STATUS_URL,
                    data=json.dumps({"status": "cancelled"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer valid.token",
                )

        self.assertEqual(resp.status_code, 200)
        notif_calls = [c for c in mock_post.call_args_list if "notifications" in str(c)]
        self.assertGreaterEqual(len(notif_calls), 1)

    def test_status_endpoint_method_not_allowed_get_405(self):
        """GET on status endpoint → 405."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.get(
                _STATUS_URL,
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(resp.status_code, 405)
