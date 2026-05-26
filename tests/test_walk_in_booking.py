"""
Tests for POST /api/bookings/walk-in — Manual / walk-in booking (grava-3432.2 / BCORE-031).

Acceptance criteria:
  1. Owner-only endpoint (role=owner). Non-owner → 403.
  2. Required: slot_id. Optional: customer_name, customer_phone, notes.
  3. Owner must own the court for the slot (slot.court_id → court.owner_id == request.user.id). 403 otherwise.
  4. slot.status must be "open" → 409 if not.
  5. Booking is inserted with:
       is_walk_in=True, status="confirmed", is_auto_approved=True
       user_id = owner's UID
  6. slots.status updated to "booked".
  7. Owner receives a confirmation notification: "Đặt sân thủ công thành công".

All Supabase HTTP calls are mocked — no real network requests.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, TestCase


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-1111-0000-0000-000000000001"
_OTHER_OWNER_ID = "bbbbbbbb-2222-0000-0000-000000000002"
_COURT_ID = "cccccccc-3333-0000-0000-000000000003"
_SLOT_ID = "dddddddd-4444-0000-0000-000000000004"
_BOOKING_ID = "eeeeeeee-5555-0000-0000-000000000005"
_PLAYER_ID = "ffffffff-6666-0000-0000-000000000006"

_OWNER_JWT_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_OTHER_OWNER_JWT_PAYLOAD = {
    "sub": _OTHER_OWNER_ID,
    "email": "other@example.com",
    "app_metadata": {"role": "owner"},
}

_PLAYER_JWT_PAYLOAD = {
    "sub": _PLAYER_ID,
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OPEN_SLOT_ROW = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": "2026-06-10T08:00:00+00:00",
    "end_at": "2026-06-10T10:00:00+00:00",
    "status": "open",
}

_BOOKED_SLOT_ROW = dict(_OPEN_SLOT_ROW, status="booked")
_BLOCKED_SLOT_ROW = dict(_OPEN_SLOT_ROW, status="blocked")

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Delta",
    "auto_approve_single": False,
}

_WALK_IN_BOOKING_ROW = {
    "id": _BOOKING_ID,
    "slot_id": _SLOT_ID,
    "user_id": _OWNER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": None,
    "customer_name": "Walk-in Customer",
    "customer_phone": "0901234567",
    "notes": None,
    "status": "confirmed",
    "is_walk_in": True,
    "is_auto_approved": True,
    "price_per_hour": None,
    "duration_minutes": None,
    "total_price": None,
    "created_at": "2026-06-10T07:00:00+00:00",
    "updated_at": "2026-06-10T07:00:00+00:00",
}


def _mock_resp(status_code: int, data):
    """Build a mock requests.Response."""
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


def _ok(data):
    return _mock_resp(200, data)


def _created(data):
    return _mock_resp(201, data)


def _make_get_side_effect(slot_row=None, court_row=None):
    """Build a GET side-effect dispatching to correct mock based on URL."""
    slot_row = slot_row or _OPEN_SLOT_ROW
    court_row = court_row or _COURT_ROW

    def get_side_effect(url, params=None, **kwargs):
        if "/slots" in url:
            return _ok([slot_row])
        if "/courts" in url:
            return _ok([court_row])
        return _ok([])

    return get_side_effect


# ---------------------------------------------------------------------------
# Happy-path: walk-in booking
# ---------------------------------------------------------------------------


class WalkInBookingHappyPathTests(TestCase):
    """POST /api/bookings/walk-in — happy path."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/walk-in"

    def _post(self, body, token="owner.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    def test_walk_in_returns_201_confirmed(self):
        """Walk-in booking → 201, status=confirmed, is_walk_in=True."""
        body = {
            "slot_id": _SLOT_ID,
            "customer_name": "Walk-in Customer",
            "customer_phone": "0901234567",
        }

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", return_value=_created([_WALK_IN_BOOKING_ROW])), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "confirmed")
        self.assertTrue(data["is_walk_in"])
        self.assertTrue(data["is_auto_approved"])

    def test_walk_in_response_has_required_fields(self):
        """Response contains all required booking fields."""
        body = {"slot_id": _SLOT_ID, "customer_name": "Walk-in Customer"}

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", return_value=_created([_WALK_IN_BOOKING_ROW])), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        for field in ("id", "slot_id", "court_id", "status", "is_walk_in", "is_auto_approved",
                      "customer_name", "customer_phone"):
            self.assertIn(field, data, f"Missing field: {field}")

    def test_walk_in_inserts_booking_with_correct_flags(self):
        """Booking insert payload has is_walk_in=True, is_auto_approved=True, status=confirmed."""
        body = {
            "slot_id": _SLOT_ID,
            "customer_name": "Walk-in Customer",
            "customer_phone": "0901234567",
        }
        captured = {}

        def capture_post(url, json=None, **kwargs):
            if "/bookings" in url:
                captured.update(json or {})
            return _created([_WALK_IN_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(captured.get("is_walk_in"), "is_walk_in must be True")
        self.assertTrue(captured.get("is_auto_approved"), "is_auto_approved must be True")
        self.assertEqual(captured.get("status"), "confirmed")
        self.assertEqual(captured.get("user_id"), _OWNER_ID)
        self.assertEqual(captured.get("customer_name"), "Walk-in Customer")
        self.assertEqual(captured.get("customer_phone"), "0901234567")

    def test_walk_in_updates_slot_to_booked(self):
        """Slot must be PATCHed to status=booked."""
        body = {"slot_id": _SLOT_ID, "customer_name": "Walk-in Customer"}
        patch_calls = []

        def capture_patch(url, json=None, params=None, **kwargs):
            if "/slots" in url:
                patch_calls.append(json or {})
            return _ok([_BOOKED_SLOT_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", return_value=_created([_WALK_IN_BOOKING_ROW])), \
             patch("bookings.views.requests.patch", side_effect=capture_patch):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            any(p.get("status") == "booked" for p in patch_calls),
            f"Expected PATCH with status=booked. Got: {patch_calls}",
        )

    def test_walk_in_sends_owner_confirmation_notification(self):
        """Walk-in → owner receives confirmation notification."""
        body = {"slot_id": _SLOT_ID, "customer_name": "Walk-in Customer"}
        notification_posts = []

        def capture_post(url, json=None, **kwargs):
            if "/notifications" in url:
                notification_posts.append(json or {})
                return _created([{"id": "notif-1"}])
            return _created([_WALK_IN_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        owner_notifs = [n for n in notification_posts if n.get("user_id") == _OWNER_ID]
        self.assertTrue(len(owner_notifs) >= 1, "Expected at least one notification to the owner.")
        body_texts = " ".join(n.get("body", "") + n.get("title", "") for n in owner_notifs)
        self.assertIn("thủ công", body_texts, "Expected 'thủ công' in walk-in notification")

    def test_walk_in_without_customer_name_still_creates_booking(self):
        """Walk-in without customer_name is allowed (customer_name is optional)."""
        body = {"slot_id": _SLOT_ID}

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", return_value=_created([dict(_WALK_IN_BOOKING_ROW, customer_name=None)])), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)

    def test_walk_in_with_notes(self):
        """Walk-in with notes → notes forwarded to booking insert."""
        body = {"slot_id": _SLOT_ID, "customer_name": "Walk-in Customer", "notes": "Pay by cash"}
        captured = {}

        def capture_post(url, json=None, **kwargs):
            if "/bookings" in url:
                captured.update(json or {})
            return _created([_WALK_IN_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(captured.get("notes"), "Pay by cash")


# ---------------------------------------------------------------------------
# Authorization — owner only, must own the court
# ---------------------------------------------------------------------------


class WalkInBookingAuthTests(TestCase):
    """POST /api/bookings/walk-in — authorization checks."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/walk-in"

    def _post(self, body, token="owner.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    def test_no_auth_returns_401(self):
        """No Authorization header → 401."""
        resp = self.client.post(
            self.url,
            data=json.dumps({"slot_id": _SLOT_ID}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post({"slot_id": _SLOT_ID})
        self.assertEqual(resp.status_code, 401)

    def test_player_role_returns_403(self):
        """Player role (non-owner) → 403 Forbidden."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT_PAYLOAD):
            resp = self._post({"slot_id": _SLOT_ID})
        self.assertEqual(resp.status_code, 403)

    def test_owner_who_does_not_own_court_returns_403(self):
        """Owner authenticated but does NOT own the court → 403."""
        # court.owner_id = _OWNER_ID, but JWT is for _OTHER_OWNER_ID
        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW])  # court owned by _OWNER_ID
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OTHER_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post({"slot_id": _SLOT_ID})

        self.assertEqual(resp.status_code, 403)
        error = resp.json().get("error", "")
        self.assertIn("own", error.lower())


# ---------------------------------------------------------------------------
# Slot unavailability — 409
# ---------------------------------------------------------------------------


class WalkInBookingSlotUnavailableTests(TestCase):
    """POST /api/bookings/walk-in — slot not open → 409."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/walk-in"

    def _post(self, body, token="owner.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_booked_slot_returns_409(self):
        """Slot already booked → 409."""
        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_BOOKED_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post({"slot_id": _SLOT_ID})

        self.assertEqual(resp.status_code, 409)

    def test_blocked_slot_returns_409(self):
        """Slot blocked → 409."""
        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_BLOCKED_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post({"slot_id": _SLOT_ID})

        self.assertEqual(resp.status_code, 409)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class WalkInBookingValidationTests(TestCase):
    """POST /api/bookings/walk-in — request body validation."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/walk-in"

    def _post(self, body, token="owner.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_missing_slot_id_returns_400(self):
        """slot_id is required → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post({})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("slot_id", resp.json().get("error", ""))

    def test_invalid_json_returns_400(self):
        """Malformed JSON body → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self.client.post(
                self.url,
                data="not-json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner.jwt.token",
            )
        self.assertEqual(resp.status_code, 400)

    def test_slot_not_found_returns_404(self):
        """Unknown slot_id → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", return_value=_ok([])):
            resp = self._post({"slot_id": "nonexistent-slot"})
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Service errors / resilience
# ---------------------------------------------------------------------------


class WalkInBookingServiceErrorTests(TestCase):
    """POST /api/bookings/walk-in — downstream failures → 503."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/walk-in"

    def _post(self, body, token="owner.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_slot_fetch_failure_returns_503(self):
        """Supabase slot fetch fails → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._post({"slot_id": _SLOT_ID})
        self.assertEqual(resp.status_code, 503)

    def test_booking_insert_failure_returns_503(self):
        """Booking insert network error → 503."""
        import requests as req_lib

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=_make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self._post({"slot_id": _SLOT_ID})
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# HTTP method guards
# ---------------------------------------------------------------------------


class WalkInBookingMethodGuardTests(TestCase):
    """Non-POST methods on /api/bookings/walk-in → 405."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/walk-in"

    def test_get_returns_405(self):
        resp = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer token")
        self.assertEqual(resp.status_code, 405)

    def test_patch_returns_405(self):
        resp = self.client.patch(
            self.url,
            data=json.dumps({}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer token",
        )
        self.assertEqual(resp.status_code, 405)
