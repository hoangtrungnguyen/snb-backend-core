"""
Tests for POST /api/bookings — Atomic single-time booking (grava-3432.1 / BCORE-030).

Acceptance criteria:
  1. SELECT ... FOR UPDATE on slots row (atomic lock via Supabase RPC).
  2. If slot.status != "open" → 409 Slot unavailable (SlotTakenFailure).
  3. Read courts.auto_approve_single for the slot's court.
  4. Insert bookings row:
       - status=confirmed, is_auto_approved=True  if auto_approve_single=True AND no booking_series_id
       - status=pending,   is_auto_approved=False otherwise
  5. Update slots.status = "booked".
  6. Commit (all via atomic Supabase RPC call).
  Notifications:
    - Manual path  → owner: "Yêu cầu đặt sân mới từ [name]"
    - Auto-approve → player: "Đặt sân thành công — [court] · [date] · [time]"
    - Auto-approve → owner:  "Đặt sân mới tự động được duyệt — [player] · [slot]"

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

_OPEN_SLOT_ROW = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": "2026-06-01T10:00:00+00:00",
    "end_at": "2026-06-01T12:00:00+00:00",
    "status": "open",
}

_BOOKED_SLOT_ROW = dict(_OPEN_SLOT_ROW, status="booked")
_BLOCKED_SLOT_ROW = dict(_OPEN_SLOT_ROW, status="blocked")

_COURT_ROW_AUTO_APPROVE = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Alpha",
    "auto_approve_single": True,
}

_COURT_ROW_MANUAL = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Alpha",
    "auto_approve_single": False,
}

_PLAYER_ROW = {
    "id": _PLAYER_ID,
    "full_name": "John Player",
    "email": "player@example.com",
    "role": "player",
}

_BOOKING_ROW_CONFIRMED = {
    "id": _BOOKING_ID,
    "slot_id": _SLOT_ID,
    "user_id": _PLAYER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": None,
    "status": "confirmed",
    "is_auto_approved": True,
    "customer_name": "John Player",
    "created_at": "2026-06-01T09:00:00+00:00",
    "updated_at": "2026-06-01T09:00:00+00:00",
}

_BOOKING_ROW_PENDING = {
    "id": _BOOKING_ID,
    "slot_id": _SLOT_ID,
    "user_id": _PLAYER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": None,
    "status": "pending",
    "is_auto_approved": False,
    "customer_name": "John Player",
    "created_at": "2026-06-01T09:00:00+00:00",
    "updated_at": "2026-06-01T09:00:00+00:00",
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


# ---------------------------------------------------------------------------
# Happy-path: auto-approve (single-time, court.auto_approve_single=True)
# ---------------------------------------------------------------------------

class AtomicBookingAutoApproveTests(TestCase):
    """POST /api/bookings — auto-approve path."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings"

    def _post(self, body, token="player.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    def _make_get_side_effect(self, slot_row=None, court_row=None, player_row=None):
        """
        Build a GET side-effect that dispatches to the right mock based on URL.
        """
        slot_row = slot_row or _OPEN_SLOT_ROW
        court_row = court_row or _COURT_ROW_AUTO_APPROVE
        player_row = player_row or _PLAYER_ROW

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([slot_row])
            if "/courts" in url:
                return _ok([court_row])
            if "/users" in url:
                return _ok([player_row])
            if "/notifications" in url:
                return _ok([])
            return _ok([])

        return get_side_effect

    # -----------------------------------------------------------------------
    # Atomic RPC path: happy-path auto-approve
    # -----------------------------------------------------------------------

    def test_auto_approve_returns_201_with_confirmed_booking(self):
        """Auto-approve single-time booking → 201, status=confirmed."""
        body = {"slot_id": _SLOT_ID}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", return_value=_created([_BOOKING_ROW_CONFIRMED])), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "confirmed")
        self.assertTrue(data["is_auto_approved"])

    def test_auto_approve_booking_has_correct_fields(self):
        """Response contains all required booking fields."""
        body = {"slot_id": _SLOT_ID}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", return_value=_created([_BOOKING_ROW_CONFIRMED])), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        for field in ("id", "slot_id", "court_id", "status", "is_auto_approved"):
            self.assertIn(field, data, f"Missing field: {field}")

    def test_auto_approve_inserts_booking_with_confirmed_status(self):
        """Booking insert payload has status=confirmed when auto-approve path."""
        body = {"slot_id": _SLOT_ID}
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        def capture_post(url, json=None, **kwargs):
            if "/bookings" in url:
                captured.update(json or {})
            return _created([_BOOKING_ROW_CONFIRMED])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(captured.get("status"), "confirmed")
        self.assertTrue(captured.get("is_auto_approved"))

    def test_auto_approve_updates_slot_status_to_booked(self):
        """Slot must be patched to status=booked after confirmed booking."""
        body = {"slot_id": _SLOT_ID}
        patch_calls = []

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        def capture_patch(url, json=None, params=None, **kwargs):
            if "/slots" in url:
                patch_calls.append(json or {})
            return _ok([dict(_OPEN_SLOT_ROW, status="booked")])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", return_value=_created([_BOOKING_ROW_CONFIRMED])), \
             patch("bookings.views.requests.patch", side_effect=capture_patch):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            any(p.get("status") == "booked" for p in patch_calls),
            f"No PATCH with status=booked found. Got: {patch_calls}",
        )

    def test_auto_approve_sends_player_notification(self):
        """Auto-approve → notification to player with success message."""
        body = {"slot_id": _SLOT_ID}
        notification_posts = []

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        def capture_post(url, json=None, **kwargs):
            if "/notifications" in url:
                notification_posts.append(json or {})
                return _created([{"id": "notif-1"}])
            return _created([_BOOKING_ROW_CONFIRMED])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        player_notifs = [n for n in notification_posts if n.get("user_id") == _PLAYER_ID]
        self.assertTrue(
            len(player_notifs) >= 1,
            "Expected at least one notification to the player.",
        )
        body_texts = " ".join(n.get("body", "") + n.get("title", "") for n in player_notifs)
        self.assertIn("Đặt sân thành công", body_texts)

    def test_auto_approve_sends_owner_notification(self):
        """Auto-approve → notification to owner with auto-approved message."""
        body = {"slot_id": _SLOT_ID}
        notification_posts = []

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        def capture_post(url, json=None, **kwargs):
            if "/notifications" in url:
                notification_posts.append(json or {})
                return _created([{"id": "notif-1"}])
            return _created([_BOOKING_ROW_CONFIRMED])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        owner_notifs = [n for n in notification_posts if n.get("user_id") == _OWNER_ID]
        self.assertTrue(
            len(owner_notifs) >= 1,
            "Expected at least one notification to the owner.",
        )
        body_texts = " ".join(n.get("body", "") + n.get("title", "") for n in owner_notifs)
        self.assertIn("tự động được duyệt", body_texts)


# ---------------------------------------------------------------------------
# Happy-path: manual approval (auto_approve_single=False)
# ---------------------------------------------------------------------------

class AtomicBookingManualApproveTests(TestCase):
    """POST /api/bookings — manual approval path."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings"

    def _post(self, body, token="player.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    def test_manual_approve_returns_201_with_pending_status(self):
        """Manual approval path → 201, status=pending, is_auto_approved=False."""
        body = {"slot_id": _SLOT_ID}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_MANUAL])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", return_value=_created([_BOOKING_ROW_PENDING])), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "pending")
        self.assertFalse(data["is_auto_approved"])

    def test_manual_approve_inserts_pending_booking(self):
        """Booking insert payload has status=pending in manual path."""
        body = {"slot_id": _SLOT_ID}
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_MANUAL])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        def capture_post(url, json=None, **kwargs):
            if "/bookings" in url:
                captured.update(json or {})
            return _created([_BOOKING_ROW_PENDING])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(captured.get("status"), "pending")
        self.assertFalse(captured.get("is_auto_approved"))

    def test_manual_approve_sends_owner_notification(self):
        """Manual path → owner gets 'Yêu cầu đặt sân mới từ' notification."""
        body = {"slot_id": _SLOT_ID}
        notification_posts = []

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_MANUAL])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        def capture_post(url, json=None, **kwargs):
            if "/notifications" in url:
                notification_posts.append(json or {})
                return _created([{"id": "notif-1"}])
            return _created([_BOOKING_ROW_PENDING])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        owner_notifs = [n for n in notification_posts if n.get("user_id") == _OWNER_ID]
        self.assertTrue(len(owner_notifs) >= 1, "Expected owner notification.")
        body_texts = " ".join(n.get("body", "") + n.get("title", "") for n in owner_notifs)
        self.assertIn("Yêu cầu đặt sân mới từ", body_texts)

    def test_series_booking_always_pending(self):
        """Booking with booking_series_id → always pending (even if auto_approve_single)."""
        body = {"slot_id": _SLOT_ID, "booking_series_id": "series-uuid-001"}
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])  # auto_approve=True
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        def capture_post(url, json=None, **kwargs):
            if "/bookings" in url:
                captured.update(json or {})
            return _created([_BOOKING_ROW_PENDING])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=capture_post), \
             patch("bookings.views.requests.patch", return_value=_ok([dict(_OPEN_SLOT_ROW, status="booked")])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        # Even with auto_approve_single=True, booking_series_id means manual path
        self.assertEqual(captured.get("status"), "pending")
        self.assertFalse(captured.get("is_auto_approved"))


# ---------------------------------------------------------------------------
# Slot unavailable — 409
# ---------------------------------------------------------------------------

class AtomicBookingSlotUnavailableTests(TestCase):
    """POST /api/bookings — slot already booked/blocked → 409."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings"

    def _post(self, body, token="player.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_booked_slot_returns_409(self):
        """Slot status=booked → 409 Slot unavailable."""
        body = {"slot_id": _SLOT_ID}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_BOOKED_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 409)
        error = resp.json().get("error", "")
        self.assertIn("unavailable", error.lower())

    def test_blocked_slot_returns_409(self):
        """Slot status=blocked → 409 Slot unavailable."""
        body = {"slot_id": _SLOT_ID}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_BLOCKED_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 409)
        error = resp.json().get("error", "")
        self.assertIn("unavailable", error.lower())

    def test_maintenance_slot_returns_409(self):
        """Slot status=maintenance → 409 Slot unavailable."""
        maintenance_row = dict(_OPEN_SLOT_ROW, status="maintenance")
        body = {"slot_id": _SLOT_ID}

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([maintenance_row])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 409)


# ---------------------------------------------------------------------------
# Authentication / authorisation
# ---------------------------------------------------------------------------

class AtomicBookingAuthTests(TestCase):
    """POST /api/bookings — auth checks."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings"

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
            resp = self.client.post(
                self.url,
                data=json.dumps({"slot_id": _SLOT_ID}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer invalid.token",
            )
        self.assertEqual(resp.status_code, 401)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class AtomicBookingValidationTests(TestCase):
    """POST /api/bookings — request body validation."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings"

    def _post(self, body, token="player.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_missing_slot_id_returns_400(self):
        """slot_id is required — missing → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post({})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("slot_id", resp.json().get("error", ""))

    def test_invalid_json_returns_400(self):
        """Malformed JSON body → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.post(
                self.url,
                data="not-json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer player.jwt.token",
            )
        self.assertEqual(resp.status_code, 400)

    def test_slot_not_found_returns_404(self):
        """slot_id doesn't exist → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", return_value=_ok([])):
            resp = self._post({"slot_id": "nonexistent-slot"})
        self.assertEqual(resp.status_code, 404)


# ---------------------------------------------------------------------------
# Service errors / resilience
# ---------------------------------------------------------------------------

class AtomicBookingServiceErrorTests(TestCase):
    """POST /api/bookings — downstream service failures → 503."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings"

    def _post(self, body, token="player.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_slot_fetch_failure_returns_503(self):
        """Supabase slot fetch fails → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._post({"slot_id": _SLOT_ID})
        self.assertEqual(resp.status_code, 503)

    def test_booking_insert_failure_returns_503(self):
        """Booking insert fails → 503."""
        import requests as req_lib

        def get_side_effect(url, params=None, **kwargs):
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])
            if "/courts" in url:
                return _ok([_COURT_ROW_AUTO_APPROVE])
            if "/users" in url:
                return _ok([_PLAYER_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self._post({"slot_id": _SLOT_ID})
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# HTTP method guards
# ---------------------------------------------------------------------------

class AtomicBookingMethodGuardTests(TestCase):
    """Non-POST methods on /api/bookings → 405."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings"

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
