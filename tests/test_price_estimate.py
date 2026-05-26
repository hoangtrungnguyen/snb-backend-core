"""
Tests for GET /api/bookings/price-estimate — Price calculation service (grava-3432.6 / BCORE-035).

Acceptance criteria:
  1. GET /bookings/price-estimate?court_id=&start_at=&end_at=&price_override=
     Returns {duration_minutes, base_price, override_price, total}.
  2. Duration = (end_at - start_at) in minutes, rounded to nearest 30.
  3. Any authenticated user may call this endpoint (player or owner).
  4. price_override is optional; when omitted, override_price is null.
  5. total = override_price if provided, else base_price.
     base_price = (duration_minutes / 60) * court.price_per_hour.
  6. Missing required params → 400.
  7. court_id not found → 404.
  8. Invalid datetime format → 400.
  9. end_at <= start_at → 400.
  10. Multi-slot: client passes merged window; backend computes single duration.

All Supabase HTTP calls are mocked — no real network requests.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_PLAYER_ID = "aaaaaaaa-1111-0000-0000-000000000001"
_OWNER_ID = "bbbbbbbb-2222-0000-0000-000000000002"
_COURT_ID = "cccccccc-3333-0000-0000-000000000003"

_PLAYER_JWT_PAYLOAD = {
    "sub": _PLAYER_ID,
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OWNER_JWT_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Alpha",
    "price_per_hour": 120000,  # 120,000 VND / hour
}

_URL = "/api/bookings/price-estimate"


def _ok_court(row=_COURT_ROW):
    """Build a mock response returning one court row."""
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = [row]
    return m


def _empty():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = []
    return m


def _error_resp(status=500):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = {}
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class PriceEstimateAuthTests(TestCase):
    """Unauthenticated requests should be rejected."""

    def test_no_token_returns_401(self):
        c = Client()
        resp = c.get(_URL, {"court_id": _COURT_ID, "start_at": "2026-06-10T08:00:00+00:00", "end_at": "2026-06-10T10:00:00+00:00"})
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        c = Client()
        resp = c.get(
            _URL,
            {"court_id": _COURT_ID, "start_at": "2026-06-10T08:00:00+00:00", "end_at": "2026-06-10T10:00:00+00:00"},
            HTTP_AUTHORIZATION="Bearer invalid-token",
        )
        self.assertEqual(resp.status_code, 401)


class PriceEstimateValidationTests(TestCase):
    """Parameter validation tests."""

    def _get(self, params, *, token_payload=_PLAYER_JWT_PAYLOAD):
        with patch("auth_ext.middleware._decode_token", return_value=token_payload):
            c = Client()
            return c.get(_URL, params, HTTP_AUTHORIZATION="Bearer test-token")

    def test_missing_court_id_returns_400(self):
        resp = self._get({"start_at": "2026-06-10T08:00:00+00:00", "end_at": "2026-06-10T10:00:00+00:00"})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("error", data)

    def test_missing_start_at_returns_400(self):
        resp = self._get({"court_id": _COURT_ID, "end_at": "2026-06-10T10:00:00+00:00"})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("error", data)

    def test_missing_end_at_returns_400(self):
        resp = self._get({"court_id": _COURT_ID, "start_at": "2026-06-10T08:00:00+00:00"})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn("error", data)

    def test_invalid_start_at_format_returns_400(self):
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "not-a-date",
            "end_at": "2026-06-10T10:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 400)

    def test_invalid_end_at_format_returns_400(self):
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "not-a-date",
        })
        self.assertEqual(resp.status_code, 400)

    def test_end_at_equal_to_start_at_returns_400(self):
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T08:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 400)

    def test_end_at_before_start_at_returns_400(self):
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T10:00:00+00:00",
            "end_at": "2026-06-10T08:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 400)

    def test_invalid_price_override_returns_400(self):
        with patch("requests.get", return_value=_ok_court()):
            resp = self._get({
                "court_id": _COURT_ID,
                "start_at": "2026-06-10T08:00:00+00:00",
                "end_at": "2026-06-10T10:00:00+00:00",
                "price_override": "not-a-number",
            })
        self.assertEqual(resp.status_code, 400)

    def test_negative_price_override_returns_400(self):
        with patch("requests.get", return_value=_ok_court()):
            resp = self._get({
                "court_id": _COURT_ID,
                "start_at": "2026-06-10T08:00:00+00:00",
                "end_at": "2026-06-10T10:00:00+00:00",
                "price_override": "-100",
            })
        self.assertEqual(resp.status_code, 400)


class PriceEstimateCourtLookupTests(TestCase):
    """Court fetch tests."""

    def _get(self, params, *, mock_get):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT_PAYLOAD):
            with patch("requests.get", side_effect=mock_get):
                c = Client()
                return c.get(_URL, params, HTTP_AUTHORIZATION="Bearer test-token")

    def test_court_not_found_returns_404(self):
        def side(url, **kw):
            return _empty()

        resp = self._get(
            {"court_id": _COURT_ID, "start_at": "2026-06-10T08:00:00+00:00", "end_at": "2026-06-10T10:00:00+00:00"},
            mock_get=side,
        )
        self.assertEqual(resp.status_code, 404)

    def test_court_service_error_returns_503(self):
        def side(url, **kw):
            return _error_resp(500)

        resp = self._get(
            {"court_id": _COURT_ID, "start_at": "2026-06-10T08:00:00+00:00", "end_at": "2026-06-10T10:00:00+00:00"},
            mock_get=side,
        )
        self.assertEqual(resp.status_code, 503)


class PriceEstimateCalculationTests(TestCase):
    """Core price calculation logic."""

    def _get(self, params, *, court_row=_COURT_ROW, token_payload=_PLAYER_JWT_PAYLOAD):
        def mock_get(url, **kw):
            return _ok_court(court_row)

        with patch("auth_ext.middleware._decode_token", return_value=token_payload):
            with patch("requests.get", side_effect=mock_get):
                c = Client()
                return c.get(_URL, params, HTTP_AUTHORIZATION="Bearer test-token")

    # ------------------------------------------------------------------
    # Duration rounding (nearest 30 minutes)
    # ------------------------------------------------------------------

    def test_exact_60_min_duration(self):
        """60 minutes → rounds to 60 (nearest 30)."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T09:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 60)

    def test_exact_90_min_duration(self):
        """90 minutes → stays 90 (already a multiple of 30)."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T09:30:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 90)

    def test_exact_120_min_duration(self):
        """2 hours = 120 min → 120."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 120)

    def test_duration_rounds_up_to_nearest_30(self):
        """75 minutes (1h15m) → rounds to 90 (nearest 30 is 90)."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T09:15:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 90)

    def test_duration_rounds_down_to_nearest_30(self):
        """110 minutes → rounds to 120 (nearest 30 is 120)."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T09:50:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 120)

    def test_duration_45_min_rounds_to_30(self):
        """45 minutes → nearest 30 is 30 (not 60), since |45-30|=15 < |45-60|=15 → tie → round half up → 60.
        Actually standard rounding: 45 / 30 = 1.5 → round to 2*30=60."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T08:45:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # 45 rounded to nearest 30 = 60 (round half up)
        self.assertIn(data["duration_minutes"], (30, 60))  # accept either rounding convention

    # ------------------------------------------------------------------
    # Base price calculation
    # ------------------------------------------------------------------

    def test_base_price_for_2_hours(self):
        """2 hours × 120,000 VND/hr = 240,000."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 120)
        self.assertAlmostEqual(data["base_price"], 240000, places=0)

    def test_base_price_for_90_minutes(self):
        """90 minutes × 120,000 VND/hr = 180,000."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T09:30:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 90)
        self.assertAlmostEqual(data["base_price"], 180000, places=0)

    def test_no_price_override_returns_null_override_price(self):
        """Without price_override, override_price should be null."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["override_price"])

    def test_no_price_override_total_equals_base_price(self):
        """Without price_override, total = base_price."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertAlmostEqual(data["total"], data["base_price"], places=0)

    # ------------------------------------------------------------------
    # Price override
    # ------------------------------------------------------------------

    def test_price_override_sets_override_price(self):
        """With price_override=150000, override_price = 150000 * 2h = 300000."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
            "price_override": "150000",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertAlmostEqual(data["override_price"], 300000, places=0)

    def test_price_override_total_uses_override(self):
        """With price_override, total = override_price (not base_price)."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
            "price_override": "150000",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertAlmostEqual(data["total"], data["override_price"], places=0)
        # base_price should still be present and correct
        self.assertAlmostEqual(data["base_price"], 240000, places=0)

    def test_zero_price_override_is_valid(self):
        """price_override=0 is valid; total = 0."""
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
            "price_override": "0",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertAlmostEqual(data["override_price"], 0, places=0)
        self.assertAlmostEqual(data["total"], 0, places=0)

    # ------------------------------------------------------------------
    # Court with no price_per_hour
    # ------------------------------------------------------------------

    def test_court_with_no_price_returns_null_base_price(self):
        """If court has no price_per_hour, base_price = null."""
        court_no_price = dict(_COURT_ROW, price_per_hour=None)
        resp = self._get(
            {
                "court_id": _COURT_ID,
                "start_at": "2026-06-10T08:00:00+00:00",
                "end_at": "2026-06-10T10:00:00+00:00",
            },
            court_row=court_no_price,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["base_price"])

    def test_court_no_price_with_override_total_uses_override(self):
        """Court has no price, but override is provided → total = override_price."""
        court_no_price = dict(_COURT_ROW, price_per_hour=None)
        resp = self._get(
            {
                "court_id": _COURT_ID,
                "start_at": "2026-06-10T08:00:00+00:00",
                "end_at": "2026-06-10T10:00:00+00:00",
                "price_override": "100000",
            },
            court_row=court_no_price,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["base_price"])
        self.assertAlmostEqual(data["override_price"], 200000, places=0)
        self.assertAlmostEqual(data["total"], 200000, places=0)

    # ------------------------------------------------------------------
    # Multi-slot window (grava-3432.6.4)
    # ------------------------------------------------------------------

    def test_multi_slot_merged_window_3_hours(self):
        """
        Client passes merged 09:00–12:00 (3 h) for two adjacent slots.
        Backend computes 180 minutes duration; base = 120000 * 3 = 360000.
        """
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T09:00:00+00:00",
            "end_at": "2026-06-10T12:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["duration_minutes"], 180)
        self.assertAlmostEqual(data["base_price"], 360000, places=0)

    # ------------------------------------------------------------------
    # Response shape
    # ------------------------------------------------------------------

    def test_response_contains_all_required_fields(self):
        resp = self._get({
            "court_id": _COURT_ID,
            "start_at": "2026-06-10T08:00:00+00:00",
            "end_at": "2026-06-10T10:00:00+00:00",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for field in ("duration_minutes", "base_price", "override_price", "total"):
            self.assertIn(field, data, msg=f"Missing field: {field}")

    # ------------------------------------------------------------------
    # Owner can also call this endpoint
    # ------------------------------------------------------------------

    def test_owner_can_call_price_estimate(self):
        resp = self._get(
            {
                "court_id": _COURT_ID,
                "start_at": "2026-06-10T08:00:00+00:00",
                "end_at": "2026-06-10T10:00:00+00:00",
            },
            token_payload=_OWNER_JWT_PAYLOAD,
        )
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # Method not allowed
    # ------------------------------------------------------------------

    def test_post_method_not_allowed(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_JWT_PAYLOAD):
            c = Client()
            resp = c.post(
                _URL,
                data=json.dumps({}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer test-token",
            )
        self.assertEqual(resp.status_code, 405)
