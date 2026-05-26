"""
Tests for POST /api/bookings/manual — Manual / walk-in booking (grava-3432.2 / BCORE-031).

Acceptance criteria:
  1. Owner-only endpoint (role=owner). Non-owner → 403.
  2. Required: court_id, date (YYYY-MM-DD), start_time (HH:MM), end_time (HH:MM).
     Optional: customer_name, customer_phone (E.164 validated), notes,
               price_per_hour_override.
  3. Owner must own the court (court.owner_id == user.id). 403 otherwise.
  4. Auto-create slot if none exists for that window.
     If slot exists and status != "open" → 409 "Giờ này đã có slot".
  5. Booking inserted with:
       is_walk_in=True, status="confirmed", is_auto_approved=True
       user_id = owner's UID
  6. price_per_hour: override if provided, else court default; total_price computed.
  7. slots.status updated to "booked".
  8. Owner receives confirmation notification: "Đặt sân thủ công thành công".

All Supabase HTTP calls are mocked — no real network requests.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-1111-0000-0000-000000000001"
_OTHER_OWNER_ID = "bbbbbbbb-2222-0000-0000-000000000002"
_COURT_ID = "cccccccc-3333-0000-0000-000000000003"
_SLOT_ID = "dddddddd-4444-0000-0000-000000000004"
_NEW_SLOT_ID = "dddddddd-4444-0000-0000-000000000099"
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

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Delta",
    "price_per_hour": 100000,
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
_NEW_SLOT_ROW = {"id": _NEW_SLOT_ID, "court_id": _COURT_ID, "status": "open"}

_WALK_IN_BOOKING_ROW = {
    "id": _BOOKING_ID,
    "slot_id": _SLOT_ID,
    "user_id": _OWNER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": None,
    "customer_name": "Walk-in Customer",
    "customer_phone": "+84901234567",
    "notes": None,
    "status": "confirmed",
    "is_walk_in": True,
    "is_auto_approved": True,
    "price_per_hour": 100000,
    "duration_minutes": 120,
    "total_price": 200000,
    "created_at": "2026-06-10T07:00:00+00:00",
    "updated_at": "2026-06-10T07:00:00+00:00",
}

# Minimal valid body for the manual booking endpoint
_VALID_BODY = {
    "court_id": _COURT_ID,
    "date": "2026-06-10",
    "start_time": "08:00",
    "end_time": "10:00",
    "customer_name": "Walk-in Customer",
    "customer_phone": "+84901234567",
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
# Happy-path: no pre-existing slot (auto-create)
# ---------------------------------------------------------------------------


class ManualBookingAutoCreateSlotTests(TestCase):
    """POST /api/bookings/manual — auto-creates slot when none exists."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/manual"

    def _post(self, body, token="owner.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    def _make_get_side_effect(self, slot_rows=None, court_row=None):
        court_row = court_row or _COURT_ROW
        slot_rows_val = slot_rows if slot_rows is not None else []

        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([court_row])
            if "/slots" in url:
                return _ok(slot_rows_val)
            return _ok([])

        return get_side_effect

    def _make_post_side_effect(self, booking_row=None, slot_row=None):
        booking_row = booking_row or _WALK_IN_BOOKING_ROW
        slot_row = slot_row or _NEW_SLOT_ROW

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([slot_row])
            if "/bookings" in url:
                return _created([booking_row])
            if "/notifications" in url:
                return _created([{"id": "notif-1"}])
            return _created([booking_row])

        return post_side_effect

    def test_returns_201_confirmed_walk_in(self):
        """Auto-create path → 201, status=confirmed, is_walk_in=True."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=self._make_post_side_effect()), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "confirmed")
        self.assertTrue(data["is_walk_in"])
        self.assertTrue(data["is_auto_approved"])

    def test_response_has_required_fields(self):
        """Response contains all required booking fields."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=self._make_post_side_effect()), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        for field in ("id", "slot_id", "court_id", "status", "is_walk_in",
                      "is_auto_approved", "customer_name", "customer_phone",
                      "price_per_hour", "duration_minutes", "total_price"):
            self.assertIn(field, data, f"Missing field: {field}")

    def test_auto_creates_slot_when_none_exists(self):
        """When no existing slot, a new slot POST is made before booking insert."""
        slot_post_calls = []
        booking_post_calls = []

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                slot_post_calls.append(json or {})
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                booking_post_calls.append(json or {})
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect(slot_rows=[])), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(slot_post_calls), 1, "Expected exactly one slot creation call")
        self.assertEqual(slot_post_calls[0].get("court_id"), _COURT_ID)
        self.assertEqual(slot_post_calls[0].get("status"), "open")

    def test_booking_insert_has_correct_flags(self):
        """Booking insert payload: is_walk_in=True, is_auto_approved=True, status=confirmed."""
        captured = {}

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                captured.update(json or {})
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(captured.get("is_walk_in"), "is_walk_in must be True")
        self.assertTrue(captured.get("is_auto_approved"), "is_auto_approved must be True")
        self.assertEqual(captured.get("status"), "confirmed")
        self.assertEqual(captured.get("user_id"), _OWNER_ID)
        self.assertEqual(captured.get("customer_name"), "Walk-in Customer")
        self.assertEqual(captured.get("customer_phone"), "+84901234567")

    def test_updates_slot_to_booked(self):
        """Slot must be PATCHed to status=booked after booking insert."""
        patch_calls = []

        def capture_patch(url, json=None, params=None, **kwargs):
            if "/slots" in url:
                patch_calls.append(json or {})
            return _ok([_BOOKED_SLOT_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=self._make_post_side_effect()), \
             patch("bookings.views.requests.patch", side_effect=capture_patch):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 201)
        self.assertTrue(
            any(p.get("status") == "booked" for p in patch_calls),
            f"Expected PATCH with status=booked. Got: {patch_calls}",
        )

    def test_sends_owner_confirmation_notification(self):
        """Owner receives 'Đặt sân thủ công thành công' notification."""
        notification_posts = []

        def post_side_effect(url, json=None, **kwargs):
            if "/notifications" in url:
                notification_posts.append(json or {})
                return _created([{"id": "notif-1"}])
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            return _created([_WALK_IN_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 201)
        owner_notifs = [n for n in notification_posts if n.get("user_id") == _OWNER_ID]
        self.assertTrue(len(owner_notifs) >= 1, "Expected at least one notification to the owner.")
        body_texts = " ".join(n.get("body", "") + n.get("title", "") for n in owner_notifs)
        self.assertIn("thủ công", body_texts)

    def test_uses_existing_open_slot_without_creating_new_one(self):
        """If an open slot already exists, use it — no new slot creation."""
        slot_post_calls = []

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                slot_post_calls.append(json or {})
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([_COURT_ROW])
            if "/slots" in url:
                return _ok([_OPEN_SLOT_ROW])  # existing open slot
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(len(slot_post_calls), 0, "Should not create slot when open slot exists")


# ---------------------------------------------------------------------------
# Price calculation
# ---------------------------------------------------------------------------


class ManualBookingPriceTests(TestCase):
    """POST /api/bookings/manual — price calculation."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/manual"

    def _post(self, body, token="owner.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def _make_get_side_effect(self, court_row=None):
        court_row = court_row or _COURT_ROW

        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([court_row])
            if "/slots" in url:
                return _ok([])
            return _ok([])

        return get_side_effect

    def _make_post_side_effect(self, booking_row=None):
        booking_row = booking_row or _WALK_IN_BOOKING_ROW

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                return _created([booking_row])
            return _created([{"id": "notif-1"}])

        return post_side_effect

    def test_price_per_hour_override_stored_in_booking(self):
        """price_per_hour_override replaces court default in booking insert."""
        body = dict(_VALID_BODY, price_per_hour_override=150000)
        captured = {}

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                captured.update(json or {})
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(captured.get("price_per_hour"), 150000.0)

    def test_court_default_price_used_when_no_override(self):
        """When no override, court default price_per_hour is used; total_price computed."""
        body = {k: v for k, v in _VALID_BODY.items()}
        captured = {}

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                captured.update(json or {})
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        # Court default is 100000, duration 120 min = 2h → total = 200000
        self.assertEqual(captured.get("price_per_hour"), 100000.0)
        self.assertEqual(captured.get("duration_minutes"), 120)
        self.assertAlmostEqual(captured.get("total_price"), 200000.0, places=1)

    def test_duration_minutes_computed_correctly(self):
        """duration_minutes = (end_time - start_time) in minutes (90 min for 08:00-09:30)."""
        body = dict(_VALID_BODY, start_time="08:00", end_time="09:30")
        captured = {}

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                captured.update(json or {})
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(captured.get("duration_minutes"), 90)

    def test_total_price_null_when_no_court_price_and_no_override(self):
        """If neither court price nor override, total_price is null."""
        court_no_price = dict(_COURT_ROW, price_per_hour=None)
        body = {k: v for k, v in _VALID_BODY.items()}
        captured = {}

        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([court_no_price])
            if "/slots" in url:
                return _ok([])
            return _ok([])

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                captured.update(json or {})
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(captured.get("total_price"))
        self.assertIsNone(captured.get("price_per_hour"))


# ---------------------------------------------------------------------------
# Slot conflict — 409
# ---------------------------------------------------------------------------


class ManualBookingSlotConflictTests(TestCase):
    """POST /api/bookings/manual — existing non-open slot → 409."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/manual"

    def _post(self, body, token="owner.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_booked_slot_returns_409_with_vietnamese_message(self):
        """Slot already booked → 409 with 'Giờ này đã có slot'."""
        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([_COURT_ROW])
            if "/slots" in url:
                return _ok([_BOOKED_SLOT_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 409)
        self.assertIn("Giờ này đã có slot", resp.json().get("error", ""))

    def test_blocked_slot_returns_409(self):
        """Slot blocked → 409."""
        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([_COURT_ROW])
            if "/slots" in url:
                return _ok([_BLOCKED_SLOT_ROW])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 409)


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class ManualBookingAuthTests(TestCase):
    """POST /api/bookings/manual — auth and ownership checks."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/manual"

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
            data=json.dumps(_VALID_BODY),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 401)

    def test_player_role_returns_403(self):
        """Player role → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT_PAYLOAD):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 403)

    def test_owner_not_owning_court_returns_403(self):
        """Owner authenticated but does NOT own the court → 403."""
        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([_COURT_ROW])  # owned by _OWNER_ID
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OTHER_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 403)
        self.assertIn("own", resp.json().get("error", "").lower())


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class ManualBookingValidationTests(TestCase):
    """POST /api/bookings/manual — request body validation."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/manual"

    def _post(self, body, token="owner.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_missing_court_id_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "court_id"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("court_id", resp.json().get("error", ""))

    def test_missing_date_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "date"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("date", resp.json().get("error", ""))

    def test_missing_start_time_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "start_time"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("start_time", resp.json().get("error", ""))

    def test_missing_end_time_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "end_time"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("end_time", resp.json().get("error", ""))

    def test_invalid_json_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self.client.post(
                self.url,
                data="not-json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner.jwt.token",
            )
        self.assertEqual(resp.status_code, 400)

    def test_end_time_before_start_time_returns_400(self):
        body = dict(_VALID_BODY, start_time="10:00", end_time="08:00")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_date_format_returns_400(self):
        body = dict(_VALID_BODY, date="10/06/2026")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_time_format_returns_400(self):
        body = dict(_VALID_BODY, start_time="8:00am")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_phone_format_returns_400(self):
        """customer_phone not in E.164 format → 400."""
        body = dict(_VALID_BODY, customer_phone="0901234567")  # missing +countrycode
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("E.164", resp.json().get("error", ""))

    def test_valid_e164_phone_accepted(self):
        """Valid E.164 phone (e.g. +84901234567) is accepted → 201."""
        body = dict(_VALID_BODY, customer_phone="+84901234567")

        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([_COURT_ROW])
            if "/slots" in url:
                return _ok([])
            return _ok([])

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            if "/bookings" in url:
                return _created([_WALK_IN_BOOKING_ROW])
            return _created([{"id": "notif-1"}])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=post_side_effect), \
             patch("bookings.views.requests.patch", return_value=_ok([_BOOKED_SLOT_ROW])):
            resp = self._post(body)

        self.assertEqual(resp.status_code, 201)

    def test_court_not_found_returns_404(self):
        """Unknown court_id → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", return_value=_ok([])):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 404)

    def test_negative_price_override_returns_400(self):
        """Negative price_per_hour_override → 400."""
        body = dict(_VALID_BODY, price_per_hour_override=-100)
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)


# ---------------------------------------------------------------------------
# Service errors / resilience
# ---------------------------------------------------------------------------


class ManualBookingServiceErrorTests(TestCase):
    """POST /api/bookings/manual — downstream failures → 503."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/manual"

    def _post(self, body, token="owner.jwt.token"):
        return self.client.post(
            self.url,
            data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_court_fetch_failure_returns_503(self):
        """Supabase court fetch network error → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 503)

    def test_booking_insert_failure_returns_503(self):
        """Booking insert network error → 503."""
        import requests as req_lib

        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([_COURT_ROW])
            if "/slots" in url:
                return _ok([])
            return _ok([])

        def post_side_effect(url, json=None, **kwargs):
            if "/slots" in url:
                return _created([_NEW_SLOT_ROW])
            raise req_lib.RequestException("timeout")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT_PAYLOAD), \
             patch("bookings.views.requests.get", side_effect=get_side_effect), \
             patch("bookings.views.requests.post", side_effect=post_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# HTTP method guards
# ---------------------------------------------------------------------------


class ManualBookingMethodGuardTests(TestCase):
    """Non-POST methods on /api/bookings/manual → 405."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/manual"

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
