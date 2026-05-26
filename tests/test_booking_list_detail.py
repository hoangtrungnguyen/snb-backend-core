"""
Tests for booking list, search & detail endpoints (grava-3432.4 / BCORE-033).

Endpoints:
  GET /api/bookings/list          — list & search bookings
  GET /api/bookings/<id>          — booking detail

Access rules:
  - Player: sees only own bookings (user_id = caller).
  - Owner:  sees all bookings for their courts.

Acceptance criteria:
  1. Player listing → only their bookings are returned.
  2. Owner listing → bookings for all of their courts are returned.
  3. Owner with no courts → empty result set.
  4. Filters: court_id, status, from_date, to_date all reduce results.
  5. Pagination: page / page_size control results.
  6. Detail: player fetches own booking → 200.
  7. Detail: player fetches another player's booking → 403.
  8. Detail: owner fetches booking for their court → 200.
  9. Detail: owner fetches booking for another owner's court → 403.
  10. Detail: non-existent booking → 404.
  11. Unauthenticated requests → 401.
  12. Downstream failures → 503.

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

_PLAYER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OWNER_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_OTHER_PLAYER_ID = "cccccccc-0000-0000-0000-000000000003"
_OTHER_OWNER_ID = "dddddddd-0000-0000-0000-000000000004"
_COURT_ID = "eeeeeeee-0000-0000-0000-000000000005"
_OTHER_COURT_ID = "ffffffff-0000-0000-0000-000000000006"
_BOOKING_ID = "11111111-0000-0000-0000-000000000007"
_OTHER_BOOKING_ID = "22222222-0000-0000-0000-000000000008"

_PLAYER_JWT = {
    "sub": _PLAYER_ID,
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OWNER_JWT = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_OTHER_PLAYER_JWT = {
    "sub": _OTHER_PLAYER_ID,
    "email": "other_player@example.com",
    "app_metadata": {"role": "player"},
}

_OTHER_OWNER_JWT = {
    "sub": _OTHER_OWNER_ID,
    "email": "other_owner@example.com",
    "app_metadata": {"role": "owner"},
}

_BOOKING_ROW = {
    "id": _BOOKING_ID,
    "slot_id": "slot-uuid-001",
    "user_id": _PLAYER_ID,
    "court_id": _COURT_ID,
    "booking_series_id": None,
    "customer_name": "Alice Player",
    "customer_phone": None,
    "notes": None,
    "status": "confirmed",
    "price_per_hour": 100.0,
    "duration_minutes": 60,
    "total_price": 100.0,
    "is_auto_approved": True,
    "is_walk_in": False,
    "created_at": "2026-06-01T09:00:00+00:00",
    "updated_at": "2026-06-01T09:00:00+00:00",
}

_OTHER_BOOKING_ROW = {
    "id": _OTHER_BOOKING_ID,
    "slot_id": "slot-uuid-002",
    "user_id": _OTHER_PLAYER_ID,
    "court_id": _OTHER_COURT_ID,
    "booking_series_id": None,
    "customer_name": "Bob Player",
    "customer_phone": None,
    "notes": None,
    "status": "pending",
    "price_per_hour": 80.0,
    "duration_minutes": 90,
    "total_price": 120.0,
    "is_auto_approved": False,
    "is_walk_in": False,
    "created_at": "2026-06-02T10:00:00+00:00",
    "updated_at": "2026-06-02T10:00:00+00:00",
}

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Alpha Court",
}

_OTHER_COURT_ROW = {
    "id": _OTHER_COURT_ID,
    "owner_id": _OTHER_OWNER_ID,
    "name": "Beta Court",
}


def _mock_resp(status_code: int, data):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


def _ok(data):
    return _mock_resp(200, data)


def _err():
    return _mock_resp(503, {"error": "service error"})


# ---------------------------------------------------------------------------
# Booking list — player perspective
# ---------------------------------------------------------------------------

class BookingListPlayerTests(TestCase):
    """GET /api/bookings/list — player sees only own bookings."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/list"

    def _get(self, params=None, token="player.jwt"):
        kwargs = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        if params:
            return self.client.get(self.url, params, **kwargs)
        return self.client.get(self.url, **kwargs)

    def test_player_gets_own_bookings(self):
        """Player list → 200 with their own bookings."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([_BOOKING_ROW])):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["id"], _BOOKING_ID)

    def test_player_list_response_structure(self):
        """Response has results, page, page_size keys."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([_BOOKING_ROW])):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertIn("page", data)
        self.assertIn("page_size", data)

    def test_player_list_empty_when_no_bookings(self):
        """Player with no bookings → empty results."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([])):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"], [])

    def test_player_list_passes_user_id_filter_to_supabase(self):
        """Player list query must scope by user_id."""
        captured_params = []

        def get_side_effect(url, params=None, **kwargs):
            captured_params.append(params)
            return _ok([_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            self._get()

        # Find the bookings call (should have user_id filter)
        booking_calls = [p for p in captured_params if p is not None and
                         any("user_id" in str(item) for item in (p if isinstance(p, list) else p.items()))]
        self.assertTrue(len(booking_calls) > 0, "Expected user_id scoping in Supabase query")

    def test_player_list_status_filter(self):
        """status= query param is forwarded to Supabase."""
        captured_params = []

        def get_side_effect(url, params=None, **kwargs):
            captured_params.append(params)
            return _ok([_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            self._get(params={"status": "confirmed"})

        params_str = str(captured_params)
        self.assertIn("confirmed", params_str)

    def test_player_list_pagination_defaults(self):
        """Default page=1, page_size=20."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([])):
            resp = self._get()

        data = resp.json()
        self.assertEqual(data["page"], 1)
        self.assertEqual(data["page_size"], 20)

    def test_player_list_custom_pagination(self):
        """Custom page / page_size are reflected in response."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([])):
            resp = self._get(params={"page": "2", "page_size": "5"})

        data = resp.json()
        self.assertEqual(data["page"], 2)
        self.assertEqual(data["page_size"], 5)

    def test_player_list_unauthenticated_returns_401(self):
        """No Authorization header → 401."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 401)

    def test_player_list_invalid_token_returns_401(self):
        """Invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self.client.get(
                self.url, HTTP_AUTHORIZATION="Bearer invalid.token"
            )
        self.assertEqual(resp.status_code, 401)

    def test_player_list_service_error_returns_503(self):
        """Supabase failure → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._get()
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# Booking list — owner perspective
# ---------------------------------------------------------------------------

class BookingListOwnerTests(TestCase):
    """GET /api/bookings/list — owner sees bookings for their courts."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/bookings/list"

    def _get(self, params=None, token="owner.jwt"):
        kwargs = {"HTTP_AUTHORIZATION": f"Bearer {token}"}
        if params:
            return self.client.get(self.url, params, **kwargs)
        return self.client.get(self.url, **kwargs)

    def _make_get_side_effect(self, court_rows=None, booking_rows=None):
        court_rows = court_rows if court_rows is not None else [_COURT_ROW]
        booking_rows = booking_rows if booking_rows is not None else [_BOOKING_ROW]

        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok(court_rows)
            if "/bookings" in url:
                return _ok(booking_rows)
            return _ok([])

        return get_side_effect

    def test_owner_gets_court_bookings(self):
        """Owner list → 200 with bookings from their courts."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=self._make_get_side_effect()):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["court_id"], _COURT_ID)

    def test_owner_with_no_courts_returns_empty(self):
        """Owner with no courts → empty results without querying bookings."""
        def get_side_effect(url, params=None, **kwargs):
            if "/courts" in url:
                return _ok([])
            # Should not reach bookings if no courts.
            return _ok([_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"], [])

    def test_owner_court_service_failure_returns_503(self):
        """Court service unavailable → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._get()
        self.assertEqual(resp.status_code, 503)

    def test_owner_list_filters_by_status(self):
        """status filter is forwarded for owner listing."""
        captured_params = []

        def get_side_effect(url, params=None, **kwargs):
            captured_params.append((url, params))
            if "/courts" in url:
                return _ok([_COURT_ROW])
            return _ok([_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            self._get(params={"status": "pending"})

        params_str = str(captured_params)
        self.assertIn("pending", params_str)

    def test_owner_list_filters_by_court_id(self):
        """court_id filter narrows results for owner."""
        captured_params = []

        def get_side_effect(url, params=None, **kwargs):
            captured_params.append((url, params))
            if "/courts" in url:
                return _ok([_COURT_ROW])
            return _ok([_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            self._get(params={"court_id": _COURT_ID})

        params_str = str(captured_params)
        self.assertIn(_COURT_ID, params_str)

    def test_owner_list_from_date_filter(self):
        """from_date query param is forwarded."""
        captured_params = []

        def get_side_effect(url, params=None, **kwargs):
            captured_params.append((url, params))
            if "/courts" in url:
                return _ok([_COURT_ROW])
            return _ok([_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            self._get(params={"from_date": "2026-06-01"})

        params_str = str(captured_params)
        self.assertIn("2026-06-01", params_str)

    def test_owner_list_to_date_filter(self):
        """to_date query param is forwarded."""
        captured_params = []

        def get_side_effect(url, params=None, **kwargs):
            captured_params.append((url, params))
            if "/courts" in url:
                return _ok([_COURT_ROW])
            return _ok([_BOOKING_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            self._get(params={"to_date": "2026-06-30"})

        params_str = str(captured_params)
        self.assertIn("2026-06-30", params_str)

    def test_owner_list_method_not_allowed_post(self):
        """POST to /api/bookings/list → 405."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT):
            resp = self.client.post(
                self.url,
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner.jwt",
            )
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Booking detail — player perspective
# ---------------------------------------------------------------------------

class BookingDetailPlayerTests(TestCase):
    """GET /api/bookings/<id> — player access control."""

    def setUp(self):
        self.client = Client()

    def _get(self, booking_id, jwt_payload=_PLAYER_JWT, token="player.jwt"):
        return self.client.get(
            f"/api/bookings/{booking_id}",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_player_fetches_own_booking_returns_200(self):
        """Player fetches their own booking → 200."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([_BOOKING_ROW])):
            resp = self._get(_BOOKING_ID)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], _BOOKING_ID)

    def test_player_detail_response_fields(self):
        """Detail response includes all standard booking fields."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([_BOOKING_ROW])):
            resp = self._get(_BOOKING_ID)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for field in (
            "id", "slot_id", "user_id", "court_id", "status",
            "is_auto_approved", "is_walk_in", "created_at", "updated_at"
        ):
            self.assertIn(field, data, f"Missing field: {field}")

    def test_player_fetches_other_player_booking_returns_403(self):
        """Player tries to access another player's booking → 403."""
        other_booking = dict(_BOOKING_ROW, user_id=_OTHER_PLAYER_ID, id=_OTHER_BOOKING_ID)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([other_booking])):
            resp = self._get(_OTHER_BOOKING_ID)

        self.assertEqual(resp.status_code, 403)
        self.assertIn("access", resp.json().get("error", "").lower())

    def test_player_detail_booking_not_found_returns_404(self):
        """Non-existent booking → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", return_value=_ok([])):
            resp = self._get("nonexistent-booking-id")

        self.assertEqual(resp.status_code, 404)

    def test_player_detail_unauthenticated_returns_401(self):
        """No token → 401."""
        resp = self.client.get(f"/api/bookings/{_BOOKING_ID}")
        self.assertEqual(resp.status_code, 401)

    def test_player_detail_invalid_token_returns_401(self):
        """Invalid token → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self.client.get(
                f"/api/bookings/{_BOOKING_ID}",
                HTTP_AUTHORIZATION="Bearer bad.token",
            )
        self.assertEqual(resp.status_code, 401)

    def test_player_detail_service_failure_returns_503(self):
        """Supabase failure → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT), \
             patch("bookings.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self._get(_BOOKING_ID)
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# Booking detail — owner perspective
# ---------------------------------------------------------------------------

class BookingDetailOwnerTests(TestCase):
    """GET /api/bookings/<id> — owner access control."""

    def setUp(self):
        self.client = Client()

    def _get_booking_then_court(self, booking_row, court_row):
        """Side-effect: first GET returns booking, subsequent GET returns court."""
        call_count = {"n": 0}

        def get_side_effect(url, params=None, **kwargs):
            call_count["n"] += 1
            if "/bookings" in url:
                return _ok([booking_row])
            if "/courts" in url:
                return _ok([court_row])
            return _ok([])

        return get_side_effect

    def test_owner_fetches_own_court_booking_returns_200(self):
        """Owner fetches booking for their court → 200."""
        se = self._get_booking_then_court(_BOOKING_ROW, _COURT_ROW)
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=se):
            resp = self.client.get(
                f"/api/bookings/{_BOOKING_ID}",
                HTTP_AUTHORIZATION="Bearer owner.jwt",
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], _BOOKING_ID)

    def test_owner_fetches_other_owners_court_booking_returns_403(self):
        """Owner tries to access booking for another owner's court → 403."""
        # Booking is for _COURT_ID; but that court's owner is _OTHER_OWNER_ID.
        other_owners_court = dict(_COURT_ROW, owner_id=_OTHER_OWNER_ID)
        se = self._get_booking_then_court(_BOOKING_ROW, other_owners_court)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=se):
            resp = self.client.get(
                f"/api/bookings/{_BOOKING_ID}",
                HTTP_AUTHORIZATION="Bearer owner.jwt",
            )

        self.assertEqual(resp.status_code, 403)

    def test_owner_detail_court_not_found_returns_403(self):
        """Court not found for booking's court_id → 403 (cannot verify ownership)."""
        def get_side_effect(url, params=None, **kwargs):
            if "/bookings" in url:
                return _ok([_BOOKING_ROW])
            if "/courts" in url:
                return _ok([])
            return _ok([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self.client.get(
                f"/api/bookings/{_BOOKING_ID}",
                HTTP_AUTHORIZATION="Bearer owner.jwt",
            )

        self.assertEqual(resp.status_code, 403)

    def test_owner_detail_booking_service_failure_returns_503(self):
        """Booking service unavailable → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.get(
                f"/api/bookings/{_BOOKING_ID}",
                HTTP_AUTHORIZATION="Bearer owner.jwt",
            )
        self.assertEqual(resp.status_code, 503)

    def test_owner_detail_court_service_failure_returns_503(self):
        """Court service unavailable during ownership check → 503."""
        import requests as req_lib
        call_count = {"n": 0}

        def get_side_effect(url, params=None, **kwargs):
            call_count["n"] += 1
            if "/bookings" in url:
                return _ok([_BOOKING_ROW])
            raise req_lib.RequestException("court service down")

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_JWT), \
             patch("bookings.views.requests.get", side_effect=get_side_effect):
            resp = self.client.get(
                f"/api/bookings/{_BOOKING_ID}",
                HTTP_AUTHORIZATION="Bearer owner.jwt",
            )
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# HTTP method guards
# ---------------------------------------------------------------------------

class BookingListMethodGuardTests(TestCase):
    """Non-GET methods on list/detail endpoints → 405."""

    def setUp(self):
        self.client = Client()

    def test_post_to_list_returns_405(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT):
            resp = self.client.post(
                "/api/bookings/list",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer token",
            )
        self.assertEqual(resp.status_code, 405)

    def test_put_to_list_returns_405(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT):
            resp = self.client.put(
                "/api/bookings/list",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer token",
            )
        self.assertEqual(resp.status_code, 405)

    def test_post_to_detail_returns_405(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT):
            resp = self.client.post(
                f"/api/bookings/{_BOOKING_ID}",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer token",
            )
        self.assertEqual(resp.status_code, 405)

    def test_patch_to_detail_returns_405(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT):
            resp = self.client.patch(
                f"/api/bookings/{_BOOKING_ID}",
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer token",
            )
        self.assertEqual(resp.status_code, 405)
