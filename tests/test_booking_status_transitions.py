"""
Tests for PATCH /api/bookings/<booking_id>/status — Booking status transitions
(grava-3432.3 / BCORE-032).

Acceptance Criteria:
  1. Owner approves pending booking → pending → confirmed
       OWNER-23: PATCH /api/bookings/<id>/status {"status": "confirmed"}
       - Only the court owner may approve.
       - Booking must be in "pending" state; other states → 409.
       - On success: booking.status = "confirmed", slot stays "booked".
       - Notification to player: "Đặt sân đã được duyệt — [court] · [slot time]"
  2. Owner rejects/cancels a booking → pending|confirmed → cancelled
       OWNER-24: PATCH /api/bookings/<id>/status {"status": "cancelled"}
       - Only the court owner may cancel (owner-cancel).
       - "pending" or "confirmed" → "cancelled"; "cancelled"/"completed" → 409.
       - Slot restored to "open" on cancellation.
       - Notification to player: "Đặt sân bị từ chối — [court] · [slot time]"
  3. Player cancels own booking → pending|confirmed → cancelled
       CAPP-052: same PATCH endpoint, player JWT.
       - Player may only cancel their own booking.
       - Other player's booking → 403.
       - Same slot→open restore and player notification as above.
       - "cancelled"/"completed" → 409.
  4. Owner marks booking completed → confirmed → completed
       - Only court owner may complete.
       - Must be "confirmed"; other states → 409.
       - Slot stays "booked" (historical).
       - Notification to player: "Sân đã hoàn thành — [court] · [slot time]"
  5. Auth:
       - No token → 401
       - Invalid token → 401
  6. Booking not found → 404
  7. Slot service error → 503

All Supabase HTTP calls are mocked — no real network requests.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest
from django.test import Client, TestCase


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_PLAYER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OWNER_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_OTHER_PLAYER_ID = "cccccccc-0000-0000-0000-000000000006"
_COURT_ID = "cccccccc-0000-0000-0000-000000000003"
_SLOT_ID = "dddddddd-0000-0000-0000-000000000004"
_BOOKING_ID = "eeeeeeee-0000-0000-0000-000000000005"

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
}

_SLOT_ROW_BOOKED = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": "2026-06-01T10:00:00+00:00",
    "end_at": "2026-06-01T12:00:00+00:00",
    "status": "booked",
}

_PENDING_BOOKING = {
    "id": _BOOKING_ID,
    "slot_id": _SLOT_ID,
    "user_id": _PLAYER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": None,
    "customer_name": "John Player",
    "customer_phone": None,
    "notes": None,
    "status": "pending",
    "price_per_hour": None,
    "duration_minutes": None,
    "total_price": None,
    "is_auto_approved": False,
    "is_walk_in": False,
    "created_at": "2026-06-01T09:00:00+00:00",
    "updated_at": "2026-06-01T09:00:00+00:00",
}

_CONFIRMED_BOOKING = dict(_PENDING_BOOKING, status="confirmed", is_auto_approved=True)
_CANCELLED_BOOKING = dict(_PENDING_BOOKING, status="cancelled")
_COMPLETED_BOOKING = dict(_CONFIRMED_BOOKING, status="completed")

_URL = f"/api/bookings/{_BOOKING_ID}/status"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_get_response(rows):
    """Return a mock requests.Response with a JSON list body."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = rows
    return mock


def _make_mock_patch_response(rows=None, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = rows or []
    return mock


# ---------------------------------------------------------------------------
# 1. Owner approves: pending → confirmed
# ---------------------------------------------------------------------------


class TestOwnerApprove(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_approve_pending_booking_returns_200(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """OWNER-23: owner approves pending → confirmed, notification sent."""
        mock_decode.return_value = _OWNER_PAYLOAD

        # GET calls: (1) booking, (2) court, (3) slot
        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
            _make_mock_get_response([_SLOT_ROW_BOOKED]),
        ]

        # PATCH booking to confirmed
        confirmed = dict(_PENDING_BOOKING, status="confirmed")
        mock_patch.return_value = _make_mock_patch_response([confirmed])

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "confirmed"
        assert data["id"] == _BOOKING_ID

        # Slot must NOT be patched to open (it stays booked on approve)
        patch_calls = mock_patch.call_args_list
        slot_patch_calls = [
            c for c in patch_calls if "slots" in str(c)
        ]
        assert len(slot_patch_calls) == 0

        # Notification sent to player
        assert mock_post.called
        notif_call = mock_post.call_args_list[0]
        notif_body = notif_call[1]["json"] if notif_call[1] else notif_call[0][1]
        assert notif_body["user_id"] == _PLAYER_ID
        assert "duyệt" in notif_body["body"].lower() or "duyệt" in notif_body["title"].lower()

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cannot_approve_confirmed_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Cannot approve a booking that is already confirmed → 409."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CONFIRMED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 409

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cannot_approve_cancelled_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Cannot approve a cancelled booking → 409."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CANCELLED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 409

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_non_owner_cannot_approve(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Player cannot approve a booking → 403."""
        mock_decode.return_value = _PLAYER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. Owner cancels: pending|confirmed → cancelled
# ---------------------------------------------------------------------------


class TestOwnerCancel(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cancels_pending_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """OWNER-24: owner cancels pending booking → cancelled, slot→open."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
            _make_mock_get_response([_SLOT_ROW_BOOKED]),
        ]

        cancelled = dict(_PENDING_BOOKING, status="cancelled")
        mock_patch.return_value = _make_mock_patch_response([cancelled])

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "cancelled"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "cancelled"

        # Slot must be restored to "open"
        patch_calls = mock_patch.call_args_list
        slot_patch_calls = [c for c in patch_calls if "slots" in str(c)]
        assert len(slot_patch_calls) >= 1
        slot_patch_body = slot_patch_calls[0][1].get("json") or slot_patch_calls[0][0][1]
        assert slot_patch_body.get("status") == "open"

        # Notification sent to player
        assert mock_post.called

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cancels_confirmed_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Owner may also cancel a confirmed booking → slot → open."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CONFIRMED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
            _make_mock_get_response([_SLOT_ROW_BOOKED]),
        ]

        cancelled = dict(_CONFIRMED_BOOKING, status="cancelled")
        mock_patch.return_value = _make_mock_patch_response([cancelled])

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "cancelled"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cannot_cancel_completed_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Cannot cancel a completed booking → 409."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_COMPLETED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "cancelled"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 409

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cannot_cancel_already_cancelled(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Cannot cancel an already-cancelled booking → 409."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CANCELLED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "cancelled"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 3. Player cancels own booking (CAPP-052)
# ---------------------------------------------------------------------------


class TestPlayerCancel(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_player_cancels_own_pending_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """CAPP-052: Player cancels own pending booking → cancelled, slot→open."""
        mock_decode.return_value = _PLAYER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
            _make_mock_get_response([_SLOT_ROW_BOOKED]),
        ]

        cancelled = dict(_PENDING_BOOKING, status="cancelled")
        mock_patch.return_value = _make_mock_patch_response([cancelled])

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "cancelled"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

        # Slot restored to open
        patch_calls = mock_patch.call_args_list
        slot_patch_calls = [c for c in patch_calls if "slots" in str(c)]
        assert len(slot_patch_calls) >= 1

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_player_cancels_own_confirmed_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Player can cancel own confirmed booking."""
        mock_decode.return_value = _PLAYER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CONFIRMED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
            _make_mock_get_response([_SLOT_ROW_BOOKED]),
        ]

        cancelled = dict(_CONFIRMED_BOOKING, status="cancelled")
        mock_patch.return_value = _make_mock_patch_response([cancelled])

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "cancelled"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 200

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_player_cannot_cancel_other_players_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Player cannot cancel another player's booking → 403."""
        mock_decode.return_value = _OTHER_PLAYER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),  # booking owned by _PLAYER_ID
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "cancelled"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 403

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_player_cannot_approve_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Player cannot approve (confirm) a booking → 403."""
        mock_decode.return_value = _PLAYER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 403

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_player_cannot_complete_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Player cannot mark a booking as completed → 403."""
        mock_decode.return_value = _PLAYER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CONFIRMED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "completed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Owner marks booking completed: confirmed → completed
# ---------------------------------------------------------------------------


class TestOwnerComplete(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_completes_confirmed_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Owner marks confirmed booking → completed. Slot stays booked."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CONFIRMED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
            _make_mock_get_response([_SLOT_ROW_BOOKED]),
        ]

        completed = dict(_CONFIRMED_BOOKING, status="completed")
        mock_patch.return_value = _make_mock_patch_response([completed])

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "completed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

        # Slot must NOT be restored to open (stays booked historically)
        patch_calls = mock_patch.call_args_list
        slot_patch_calls = [c for c in patch_calls if "slots" in str(c)]
        assert len(slot_patch_calls) == 0

        # Notification sent to player
        assert mock_post.called

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cannot_complete_pending_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Cannot complete a pending booking → 409."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "completed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 409

    @patch("bookings.views.requests.post")
    @patch("bookings.views.requests.get")
    @patch("bookings.views.requests.patch")
    @patch("auth_ext.middleware._decode_token")
    def test_owner_cannot_complete_cancelled_booking(
        self, mock_decode, mock_patch, mock_get, mock_post
    ):
        """Cannot complete a cancelled booking → 409."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_CANCELLED_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "completed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 5. Auth errors
# ---------------------------------------------------------------------------


class TestAuthErrors(TestCase):
    def setUp(self):
        self.client = Client()

    def test_no_token_returns_401(self):
        """Missing Authorization header → 401."""
        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    @patch("auth_ext.middleware._decode_token")
    def test_invalid_token_returns_401(self, mock_decode):
        """Invalid token → 401."""
        mock_decode.return_value = None

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer badtoken",
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 6. Booking not found
# ---------------------------------------------------------------------------


class TestBookingNotFound(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.get")
    @patch("auth_ext.middleware._decode_token")
    def test_booking_not_found_returns_404(self, mock_decode, mock_get):
        """Non-existent booking → 404."""
        mock_decode.return_value = _OWNER_PAYLOAD
        mock_get.return_value = _make_mock_get_response([])  # empty result

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. Invalid status value
# ---------------------------------------------------------------------------


class TestInvalidStatus(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.get")
    @patch("auth_ext.middleware._decode_token")
    def test_invalid_status_returns_400(self, mock_decode, mock_get):
        """Unsupported target status → 400."""
        mock_decode.return_value = _OWNER_PAYLOAD
        mock_get.return_value = _make_mock_get_response([_PENDING_BOOKING])

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "invalid_status"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 400

    @patch("auth_ext.middleware._decode_token")
    def test_missing_status_returns_400(self, mock_decode):
        """Missing status field → 400."""
        mock_decode.return_value = _OWNER_PAYLOAD

        resp = self.client.patch(
            _URL,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 400

    @patch("auth_ext.middleware._decode_token")
    def test_invalid_json_returns_400(self, mock_decode):
        """Invalid JSON → 400."""
        mock_decode.return_value = _OWNER_PAYLOAD

        resp = self.client.patch(
            _URL,
            data="not json",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 8. Court not owned by requester (owner of different court tries to approve)
# ---------------------------------------------------------------------------


class TestCrossCourtAccess(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.get")
    @patch("auth_ext.middleware._decode_token")
    def test_other_owner_cannot_approve(self, mock_decode, mock_get):
        """A different owner (not the court owner) cannot approve → 403."""
        other_owner_id = "ffffffff-0000-0000-0000-999999999999"
        mock_decode.return_value = {
            "sub": other_owner_id,
            "email": "other_owner@example.com",
            "app_metadata": {"role": "owner"},
        }

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),  # court.owner_id = _OWNER_ID != other_owner_id
        ]

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 9. Service unavailable (Supabase error)
# ---------------------------------------------------------------------------


class TestServiceErrors(TestCase):
    def setUp(self):
        self.client = Client()

    @patch("bookings.views.requests.get")
    @patch("auth_ext.middleware._decode_token")
    def test_booking_service_error_returns_503(self, mock_decode, mock_get):
        """Supabase error when fetching booking → 503."""
        mock_decode.return_value = _OWNER_PAYLOAD

        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.json.return_value = {}
        mock_get.return_value = error_resp

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 503

    @patch("bookings.views.requests.patch")
    @patch("bookings.views.requests.get")
    @patch("auth_ext.middleware._decode_token")
    def test_booking_patch_service_error_returns_503(
        self, mock_decode, mock_get, mock_patch
    ):
        """Supabase error when patching booking status → 503."""
        mock_decode.return_value = _OWNER_PAYLOAD

        mock_get.side_effect = [
            _make_mock_get_response([_PENDING_BOOKING]),
            _make_mock_get_response([_COURT_ROW]),
            _make_mock_get_response([_SLOT_ROW_BOOKED]),
        ]

        error_resp = MagicMock()
        error_resp.status_code = 500
        error_resp.json.return_value = {}
        mock_patch.return_value = error_resp

        resp = self.client.patch(
            _URL,
            data=json.dumps({"status": "confirmed"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer validtoken",
        )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 10. Method not allowed
# ---------------------------------------------------------------------------


class TestMethodNotAllowed(TestCase):
    def setUp(self):
        self.client = Client()

    def test_get_method_not_allowed(self):
        """GET on status endpoint → 405."""
        resp = self.client.get(_URL)
        assert resp.status_code == 405

    def test_post_method_not_allowed(self):
        """POST on status endpoint → 405."""
        resp = self.client.post(_URL, data="{}", content_type="application/json")
        assert resp.status_code == 405
