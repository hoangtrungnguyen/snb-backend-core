"""
Tests for Play-together access control (grava-3432.5 / BCORE-034).

Endpoints covered:
  PATCH /api/slots/{id}/access                     — booking owner sets access_policy / max_players
  POST  /api/slots/{id}/join                       — player requests to join a slot
  PATCH /api/slot-join-requests/{id}/approve       — slot owner approves join request
  PATCH /api/slot-join-requests/{id}/reject        — slot owner rejects join request
  GET   /api/slots/{id}/participants               — list participants + pending requests (owner only)
  GET   /api/slots/{id}/join-status?user_id=       — check requester's join status

Surfaces: CAPP-046, CAPP-053, CAPP-054.

All Supabase HTTP calls are mocked — no real network requests.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest
from django.test import Client

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_OWNER_ID   = "aaaaaaaa-0000-0000-0000-000000000001"
_PLAYER_ID  = "bbbbbbbb-0000-0000-0000-000000000002"
_PLAYER2_ID = "cccccccc-0000-0000-0000-000000000003"
_OTHER_ID   = "dddddddd-0000-0000-0000-000000000004"
_COURT_ID   = "eeeeeeee-0000-0000-0000-000000000005"
_SLOT_ID    = "ffffffff-0000-0000-0000-000000000006"
_BOOKING_ID = "11111111-0000-0000-0000-000000000007"
_JOIN_REQ_ID = "22222222-0000-0000-0000-000000000008"
_PARTICIPANT_ID = "33333333-0000-0000-0000-000000000009"

# JWT payloads
_OWNER_JWT = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}
_PLAYER_JWT = {
    "sub": _PLAYER_ID,
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}
_PLAYER2_JWT = {
    "sub": _PLAYER2_ID,
    "email": "player2@example.com",
    "app_metadata": {"role": "player"},
}
_OTHER_JWT = {
    "sub": _OTHER_ID,
    "email": "other@example.com",
    "app_metadata": {"role": "player"},
}

# DB rows
_SLOT_ROW = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": "2026-06-01T10:00:00+00:00",
    "end_at": "2026-06-01T12:00:00+00:00",
    "status": "booked",
    "access_policy": "open",
    "max_players": None,
    "blocked_reason": None,
    "booking_id": _BOOKING_ID,
    "notes": None,
}

_BOOKING_ROW = {
    "id": _BOOKING_ID,
    "slot_id": _SLOT_ID,
    "user_id": _OWNER_ID,   # _OWNER_ID is the booking owner
    "court_id": _COURT_ID,
    "status": "confirmed",
}

_JOIN_REQUEST_ROW = {
    "id": _JOIN_REQ_ID,
    "slot_id": _SLOT_ID,
    "user_id": _PLAYER_ID,
    "status": "pending",
    "requested_at": "2026-06-01T08:00:00+00:00",
}

_PARTICIPANT_ROW = {
    "id": _PARTICIPANT_ID,
    "slot_id": _SLOT_ID,
    "user_id": _PLAYER2_ID,
    "joined_at": "2026-06-01T08:30:00+00:00",
    "payment_status": "unpaid",
    "payment_method": None,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _token_for(payload: dict) -> str:
    return "Bearer test-token"


def _auth_header(payload: dict) -> str:
    return "Bearer test-token"


def _mock_decode(token: str, payload: dict):
    """Return a mock _decode_token that yields payload."""
    return lambda t: payload


def _make_response(data, status_code=200):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


# ===========================================================================
# PATCH /api/slots/{id}/access
# ===========================================================================

class TestSlotAccessPatch:
    """
    PATCH /api/slots/{id}/access
    Booking owner sets access_policy (open|private) and max_players.
    """

    def _patch_access(self, client, slot_id, body, jwt_payload):
        with patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("test-token", jwt_payload)):
            return client.patch(
                f"/api/slots/{slot_id}/access",
                data=json.dumps(body),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

    def test_200_booking_owner_sets_policy_open(self):
        """Booking owner can set access_policy=open."""
        client = Client()
        updated_slot = dict(_SLOT_ROW, access_policy="open", max_players=4)

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
            patch("requests.patch") as mock_patch,
        ):
            # slot fetch → booking fetch
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),          # GET slot
                _make_response([_BOOKING_ROW]),       # GET booking for slot
            ]
            mock_patch.return_value = _make_response([updated_slot])

            resp = client.patch(
                f"/api/slots/{_SLOT_ID}/access",
                data=json.dumps({"access_policy": "open", "max_players": 4}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["access_policy"] == "open"
        assert data["max_players"] == 4

    def test_200_booking_owner_sets_policy_private(self):
        """Booking owner can set access_policy=private."""
        client = Client()
        updated_slot = dict(_SLOT_ROW, access_policy="private", max_players=None)

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
            patch("requests.patch") as mock_patch,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            mock_patch.return_value = _make_response([updated_slot])

            resp = client.patch(
                f"/api/slots/{_SLOT_ID}/access",
                data=json.dumps({"access_policy": "private"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert resp.json()["access_policy"] == "private"

    def test_401_unauthenticated(self):
        """No token → 401."""
        client = Client()
        resp = client.patch(
            f"/api/slots/{_SLOT_ID}/access",
            data=json.dumps({"access_policy": "open"}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_403_non_booking_owner(self):
        """User who didn't create the booking → 403."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),   # booking owner is _OWNER_ID, not _PLAYER_ID
            ]
            resp = client.patch(
                f"/api/slots/{_SLOT_ID}/access",
                data=json.dumps({"access_policy": "open"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 403

    def test_400_invalid_access_policy(self):
        """access_policy must be 'open' or 'private'."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            resp = client.patch(
                f"/api/slots/{_SLOT_ID}/access",
                data=json.dumps({"access_policy": "invalid"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 400

    def test_400_missing_fields(self):
        """Empty body (no access_policy) → 400."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            resp = client.patch(
                f"/api/slots/{_SLOT_ID}/access",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 400

    def test_404_slot_not_found(self):
        """Non-existent slot → 404."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.return_value = _make_response([])
            resp = client.patch(
                f"/api/slots/nonexistent/access",
                data=json.dumps({"access_policy": "open"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 404

    def test_404_no_booking_for_slot(self):
        """Slot exists but has no booking (can't determine booking owner) → 404."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([]),   # no booking found
            ]
            resp = client.patch(
                f"/api/slots/{_SLOT_ID}/access",
                data=json.dumps({"access_policy": "open"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 404

    def test_405_get_not_allowed(self):
        """GET is not allowed on this endpoint."""
        client = Client()
        with patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)):
            resp = client.get(f"/api/slots/{_SLOT_ID}/access", HTTP_AUTHORIZATION="Bearer test-token")
        assert resp.status_code == 405


# ===========================================================================
# POST /api/slots/{id}/join
# ===========================================================================

class TestSlotJoin:
    """
    POST /api/slots/{id}/join
    Player requests to join an open slot (CAPP-054).
    Creates slot_join_requests row with status=pending.
    """

    def test_201_player_joins_open_slot(self):
        """Authenticated player joins an open slot → 201 with pending join request."""
        client = Client()
        created_req = dict(_JOIN_REQUEST_ROW)

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
            patch("requests.post") as mock_post,
        ):
            mock_get.side_effect = [
                _make_response([dict(_SLOT_ROW, access_policy="open")]),  # GET slot
                _make_response([]),   # GET existing join requests → none
            ]
            mock_post.return_value = _make_response([created_req], 201)

            resp = client.post(
                f"/api/slots/{_SLOT_ID}/join",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["slot_id"] == _SLOT_ID
        assert data["user_id"] == _PLAYER_ID
        assert data["status"] == "pending"

    def test_409_slot_access_policy_private(self):
        """Slot with access_policy=private → 409 (cannot join)."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([dict(_SLOT_ROW, access_policy="private")]),
            ]

            resp = client.post(
                f"/api/slots/{_SLOT_ID}/join",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 409

    def test_409_already_joined(self):
        """Player already has a pending/approved join request → 409."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            # slot fetch then existing join request check
            mock_get.side_effect = [
                _make_response([dict(_SLOT_ROW, access_policy="open")]),
                _make_response([_JOIN_REQUEST_ROW]),  # existing pending request
            ]
            resp = client.post(
                f"/api/slots/{_SLOT_ID}/join",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 409

    def test_404_slot_not_found(self):
        """Non-existent slot → 404."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [_make_response([])]
            resp = client.post(
                f"/api/slots/nonexistent/join",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 404

    def test_401_unauthenticated(self):
        """No token → 401."""
        client = Client()
        resp = client.post(
            f"/api/slots/{_SLOT_ID}/join",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_405_get_not_allowed(self):
        """GET is not allowed on this endpoint."""
        client = Client()
        with patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)):
            resp = client.get(f"/api/slots/{_SLOT_ID}/join", HTTP_AUTHORIZATION="Bearer test-token")
        assert resp.status_code == 405


# ===========================================================================
# PATCH /api/slot-join-requests/{id}/approve
# ===========================================================================

class TestJoinRequestApprove:
    """
    PATCH /api/slot-join-requests/{id}/approve
    Slot owner approves a pending join request.
    Inserts slot_participants row. Notifies requester.
    Surfaces: CAPP-053.
    """

    def test_200_owner_approves_request(self):
        """Slot booking owner approves a pending join request → 200."""
        client = Client()
        approved_req = dict(_JOIN_REQUEST_ROW, status="approved")
        participant = dict(_PARTICIPANT_ROW, user_id=_PLAYER_ID)

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
            patch("requests.patch") as mock_patch,
            patch("requests.post") as mock_post,
        ):
            # join_request → slot → booking
            mock_get.side_effect = [
                _make_response([_JOIN_REQUEST_ROW]),  # fetch join request
                _make_response([_SLOT_ROW]),           # fetch slot
                _make_response([_BOOKING_ROW]),        # fetch booking (owner check)
            ]
            mock_patch.return_value = _make_response([approved_req])
            mock_post.return_value = _make_response([participant], 201)

            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/approve",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"

    def test_403_non_slot_owner(self):
        """Non-booking-owner cannot approve → 403."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER2_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_JOIN_REQUEST_ROW]),
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),   # booking owner = _OWNER_ID, not _PLAYER2_ID
            ]
            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/approve",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 403

    def test_404_join_request_not_found(self):
        """Non-existent join request → 404."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.return_value = _make_response([])
            resp = client.patch(
                f"/api/slot-join-requests/nonexistent/approve",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 404

    def test_409_already_processed(self):
        """Approving an already-approved or rejected request → 409."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([dict(_JOIN_REQUEST_ROW, status="approved")]),
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/approve",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 409

    def test_401_unauthenticated(self):
        """No token → 401."""
        client = Client()
        resp = client.patch(
            f"/api/slot-join-requests/{_JOIN_REQ_ID}/approve",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_notification_sent_to_requester(self):
        """Approval must fire notification to the requester."""
        client = Client()
        approved_req = dict(_JOIN_REQUEST_ROW, status="approved")
        participant = dict(_PARTICIPANT_ROW, user_id=_PLAYER_ID)

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
            patch("requests.patch") as mock_patch,
            patch("requests.post") as mock_post,
        ):
            mock_get.side_effect = [
                _make_response([_JOIN_REQUEST_ROW]),
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            mock_patch.return_value = _make_response([approved_req])
            mock_post.return_value = _make_response([participant], 201)

            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/approve",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        # notification POST should be called (participants insert + notification)
        assert mock_post.called


# ===========================================================================
# PATCH /api/slot-join-requests/{id}/reject
# ===========================================================================

class TestJoinRequestReject:
    """
    PATCH /api/slot-join-requests/{id}/reject
    Slot owner rejects a pending join request.
    Sets status=rejected. Notifies requester.
    """

    def test_200_owner_rejects_request(self):
        """Slot booking owner rejects a pending join request → 200."""
        client = Client()
        rejected_req = dict(_JOIN_REQUEST_ROW, status="rejected")

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
            patch("requests.patch") as mock_patch,
            patch("requests.post") as mock_post,
        ):
            mock_get.side_effect = [
                _make_response([_JOIN_REQUEST_ROW]),
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            mock_patch.return_value = _make_response([rejected_req])
            mock_post.return_value = _make_response({}, 201)

            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/reject",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_403_non_slot_owner(self):
        """Non-booking-owner cannot reject → 403."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER2_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_JOIN_REQUEST_ROW]),
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/reject",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 403

    def test_404_join_request_not_found(self):
        """Non-existent join request → 404."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.return_value = _make_response([])
            resp = client.patch(
                f"/api/slot-join-requests/nonexistent/reject",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 404

    def test_409_already_processed(self):
        """Rejecting an already-rejected request → 409."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([dict(_JOIN_REQUEST_ROW, status="rejected")]),
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/reject",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 409

    def test_401_unauthenticated(self):
        """No token → 401."""
        client = Client()
        resp = client.patch(
            f"/api/slot-join-requests/{_JOIN_REQ_ID}/reject",
            data=json.dumps({}),
            content_type="application/json",
        )
        assert resp.status_code == 401

    def test_notification_sent_to_requester(self):
        """Rejection must fire notification to the requester."""
        client = Client()
        rejected_req = dict(_JOIN_REQUEST_ROW, status="rejected")

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
            patch("requests.patch") as mock_patch,
            patch("requests.post") as mock_post,
        ):
            mock_get.side_effect = [
                _make_response([_JOIN_REQUEST_ROW]),
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
            ]
            mock_patch.return_value = _make_response([rejected_req])
            mock_post.return_value = _make_response({}, 201)

            resp = client.patch(
                f"/api/slot-join-requests/{_JOIN_REQ_ID}/reject",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert mock_post.called


# ===========================================================================
# GET /api/slots/{id}/participants
# ===========================================================================

class TestSlotParticipants:
    """
    GET /api/slots/{id}/participants
    Lists confirmed participants + pending join requests.
    Only the slot booking owner can see all. Players see their own entry.
    """

    def test_200_owner_sees_participants_and_requests(self):
        """Booking owner gets full list: confirmed participants + pending requests."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),          # slot
                _make_response([_BOOKING_ROW]),       # booking (owner check)
                _make_response([_PARTICIPANT_ROW]),   # slot_participants
                _make_response([_JOIN_REQUEST_ROW]),  # slot_join_requests (pending)
            ]
            resp = client.get(
                f"/api/slots/{_SLOT_ID}/participants",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "participants" in data
        assert "join_requests" in data
        assert len(data["participants"]) == 1
        assert len(data["join_requests"]) == 1

    def test_200_player_sees_own_join_status(self):
        """Non-owner authenticated user can also call this — sees participants list."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([_BOOKING_ROW]),
                _make_response([_PARTICIPANT_ROW]),
                _make_response([_JOIN_REQUEST_ROW]),
            ]
            resp = client.get(
                f"/api/slots/{_SLOT_ID}/participants",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200

    def test_401_unauthenticated(self):
        """No token → 401."""
        client = Client()
        resp = client.get(f"/api/slots/{_SLOT_ID}/participants")
        assert resp.status_code == 401

    def test_404_slot_not_found(self):
        """Non-existent slot → 404."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.return_value = _make_response([])
            resp = client.get(
                f"/api/slots/nonexistent/participants",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 404

    def test_post_empty_body_returns_400(self):
        """POST is now handled (BCORE-304); empty body → 400 not 405."""
        client = Client()
        with patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _OWNER_JWT)):
            resp = client.post(
                f"/api/slots/{_SLOT_ID}/participants",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )
        assert resp.status_code == 400


# ===========================================================================
# GET /api/slots/{id}/join-status?user_id=
# ===========================================================================

class TestSlotJoinStatus:
    """
    GET /api/slots/{id}/join-status?user_id=
    Returns the requester's join status: pending|approved|rejected|none.
    Used by CAPP-054 "Yêu cầu chơi cùng" badge.
    """

    def test_200_pending_status(self):
        """Player has a pending join request → status=pending."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([_JOIN_REQUEST_ROW]),  # pending
            ]
            resp = client.get(
                f"/api/slots/{_SLOT_ID}/join-status?user_id={_PLAYER_ID}",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    def test_200_approved_status(self):
        """Player has approved join request → status=approved."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([dict(_JOIN_REQUEST_ROW, status="approved")]),
            ]
            resp = client.get(
                f"/api/slots/{_SLOT_ID}/join-status?user_id={_PLAYER_ID}",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"

    def test_200_rejected_status(self):
        """Player has rejected join request → status=rejected."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([dict(_JOIN_REQUEST_ROW, status="rejected")]),
            ]
            resp = client.get(
                f"/api/slots/{_SLOT_ID}/join-status?user_id={_PLAYER_ID}",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "rejected"

    def test_200_none_status(self):
        """Player has no join request → status=none."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([]),  # no join request
            ]
            resp = client.get(
                f"/api/slots/{_SLOT_ID}/join-status?user_id={_PLAYER_ID}",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "none"

    def test_401_unauthenticated(self):
        """No token → 401."""
        client = Client()
        resp = client.get(f"/api/slots/{_SLOT_ID}/join-status?user_id={_PLAYER_ID}")
        assert resp.status_code == 401

    def test_404_slot_not_found(self):
        """Non-existent slot → 404."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.return_value = _make_response([])
            resp = client.get(
                f"/api/slots/nonexistent/join-status?user_id={_PLAYER_ID}",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 404

    def test_uses_authenticated_user_when_no_user_id_param(self):
        """When user_id query param is omitted, defaults to authenticated user's ID."""
        client = Client()

        with (
            patch("auth_ext.middleware._decode_token", side_effect=_mock_decode("", _PLAYER_JWT)),
            patch("requests.get") as mock_get,
        ):
            mock_get.side_effect = [
                _make_response([_SLOT_ROW]),
                _make_response([]),
            ]
            resp = client.get(
                f"/api/slots/{_SLOT_ID}/join-status",
                HTTP_AUTHORIZATION="Bearer test-token",
            )

        assert resp.status_code == 200
        assert resp.json()["status"] == "none"
