"""
Tests for POST /api/courts/{id}/recurrence endpoint (grava-3106.4).

BCORE-023 — Recurring slot schedule generation (OWNER-20).

This endpoint generates open-availability *slots* on the owner's calendar
from a recurrence rule. It is distinct from BCORE-036 which generates
*bookings* against existing slots.

Request body schema:
  {
    "days_of_week":  ["mon", "wed", "fri"],   # required — which weekdays to create slots on
    "start_time":    "09:00",                  # required — slot start time (HH:MM)
    "end_time":      "11:00",                  # required — slot end time (HH:MM)
    "from_date":     "2026-06-01",             # required — first day of recurrence (YYYY-MM-DD)
    "until_date":    "2026-06-30"              # required — last day (inclusive)
  }

Response 200:
  {
    "created": <int>,   # number of slots created
    "skipped": <int>,   # number of occurrences skipped (overlap or outside hours)
    "slots":   [...]    # array of created slot objects
  }

Acceptance criteria (derived from grava-3106.4 / BCORE-023 / OWNER-20):
  AC1  Owner-only — 401 if no auth, 403 if non-owner.
  AC2  Court must exist and belong to the authenticated owner (404 if not found, 403 if wrong owner).
  AC3  Required fields validated — 400 if any missing or malformed.
  AC4  days_of_week must contain only valid day keys (mon..sun).
  AC5  start_time / end_time must be valid HH:MM; end_time > start_time.
  AC6  from_date / until_date must be valid YYYY-MM-DD; until_date >= from_date.
  AC7  Each occurrence within [from_date, until_date] on a matching weekday produces one slot,
       provided the time falls within the court's operating_hours.
  AC8  Overlapping slots are silently skipped (counted in `skipped`).
  AC9  Slots outside operating_hours for that day are silently skipped (counted in `skipped`).
  AC10 Returns 200 with {created, skipped, slots} even when created == 0 (all skipped).
  AC11 Maximum 90 days look-ahead (until_date − from_date > 90 days → 400).
"""
import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, call

from django.test import TestCase, Client


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-1111-1111-1111-000000000001"
_OTHER_OWNER_ID = "bbbbbbbb-2222-2222-2222-000000000002"
_COURT_ID = "cccccccc-3333-3333-3333-000000000003"

_OWNER_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}
_OTHER_OWNER_PAYLOAD = {
    "sub": _OTHER_OWNER_ID,
    "email": "other@example.com",
    "app_metadata": {"role": "owner"},
}
_PLAYER_PAYLOAD = {
    "sub": "dddddddd-4444-4444-4444-000000000004",
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OPERATING_HOURS = {
    "mon": {"open": "08:00", "close": "22:00"},
    "tue": {"open": "08:00", "close": "22:00"},
    "wed": {"open": "08:00", "close": "22:00"},
    "thu": {"open": "08:00", "close": "22:00"},
    "fri": {"open": "08:00", "close": "22:00"},
    "sat": {"open": "09:00", "close": "21:00"},
    "sun": {"open": "09:00", "close": "21:00"},
}

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "operating_hours": _OPERATING_HOURS,
}

# 2026-06-01 is a Monday; 2026-06-05 is a Friday; 2026-06-07 is a Sunday.
_FROM_DATE = "2026-06-01"   # Monday
_UNTIL_DATE = "2026-06-07"  # Sunday  — 1 full week

_VALID_BODY = {
    "days_of_week": ["mon", "wed", "fri"],
    "start_time": "09:00",
    "end_time": "11:00",
    "from_date": _FROM_DATE,
    "until_date": _UNTIL_DATE,
}


def _mock_resp(status_code: int, data):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


def _court_resp(court_row=None):
    return _mock_resp(200, [court_row or _COURT_ROW])


def _no_court_resp():
    return _mock_resp(200, [])


def _no_overlap():
    return _mock_resp(200, [])


def _overlap():
    return _mock_resp(200, [{"id": "existing-slot"}])


def _slot_created(idx=0):
    """Simulated Supabase response for slot creation."""
    return _mock_resp(201, [{
        "id": f"slot-{idx:04d}",
        "court_id": _COURT_ID,
        "start_at": f"2026-06-0{idx + 1}T09:00:00+00:00",
        "end_at": f"2026-06-0{idx + 1}T11:00:00+00:00",
        "status": "open",
        "is_owner_slot": False,
        "access_policy": None,
        "max_players": None,
        "blocked_reason": None,
        "created_at": "2026-06-01T00:00:00Z",
        "updated_at": "2026-06-01T00:00:00Z",
    }])


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class RecurrenceScheduleTests(TestCase):
    """Tests for POST /api/courts/{id}/recurrence."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/courts/{_COURT_ID}/recurrence"

    def _post(self, body, token="owner.jwt.token", auth=True):
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    # ------------------------------------------------------------------
    # AC1 — Authentication / authorisation
    # ------------------------------------------------------------------

    def test_no_auth_header_returns_401(self):
        """No Authorization header → 401."""
        resp = self._post(_VALID_BODY, auth=False)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Invalid JWT → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 401)

    def test_player_role_returns_403(self):
        """Player (non-owner) → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # AC2 — Court existence and ownership
    # ------------------------------------------------------------------

    def test_court_not_found_returns_404(self):
        """Non-existent court_id → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_no_court_resp()):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 404)

    def test_wrong_owner_returns_403(self):
        """Court exists but belongs to a different owner → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_OTHER_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_court_resp()):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 403)

    def test_supabase_court_fetch_failure_returns_503(self):
        """Supabase unavailable on court fetch → 503."""
        import requests as req_lib
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=req_lib.RequestException("down")):
            resp = self._post(_VALID_BODY)
        self.assertEqual(resp.status_code, 503)

    # ------------------------------------------------------------------
    # AC3 — Required field validation
    # ------------------------------------------------------------------

    def test_invalid_json_returns_400(self):
        """Malformed JSON body → 400."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.post(
                self.url,
                data="not-json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner.jwt.token",
            )
        self.assertEqual(resp.status_code, 400)

    def test_missing_days_of_week_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "days_of_week"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("days_of_week", resp.json().get("error", ""))

    def test_missing_start_time_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "start_time"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("start_time", resp.json().get("error", ""))

    def test_missing_end_time_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "end_time"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("end_time", resp.json().get("error", ""))

    def test_missing_from_date_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "from_date"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("from_date", resp.json().get("error", ""))

    def test_missing_until_date_returns_400(self):
        body = {k: v for k, v in _VALID_BODY.items() if k != "until_date"}
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("until_date", resp.json().get("error", ""))

    # ------------------------------------------------------------------
    # AC4 — days_of_week validation
    # ------------------------------------------------------------------

    def test_empty_days_of_week_returns_400(self):
        body = dict(_VALID_BODY, days_of_week=[])
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_day_key_returns_400(self):
        body = dict(_VALID_BODY, days_of_week=["mon", "holiday"])
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_days_of_week_not_a_list_returns_400(self):
        body = dict(_VALID_BODY, days_of_week="mon")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # AC5 — start_time / end_time validation
    # ------------------------------------------------------------------

    def test_invalid_start_time_format_returns_400(self):
        body = dict(_VALID_BODY, start_time="9am")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_end_time_format_returns_400(self):
        body = dict(_VALID_BODY, end_time="25:00")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_end_time_before_start_time_returns_400(self):
        body = dict(_VALID_BODY, start_time="11:00", end_time="09:00")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_end_time_equal_start_time_returns_400(self):
        body = dict(_VALID_BODY, start_time="09:00", end_time="09:00")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # AC6 — from_date / until_date validation
    # ------------------------------------------------------------------

    def test_invalid_from_date_format_returns_400(self):
        body = dict(_VALID_BODY, from_date="06/01/2026")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_invalid_until_date_format_returns_400(self):
        body = dict(_VALID_BODY, until_date="2026-13-01")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    def test_until_date_before_from_date_returns_400(self):
        body = dict(_VALID_BODY, from_date="2026-06-10", until_date="2026-06-01")
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # AC11 — 90-day maximum look-ahead
    # ------------------------------------------------------------------

    def test_range_over_90_days_returns_400(self):
        """until_date − from_date > 90 days → 400."""
        from_d = "2026-06-01"
        until_d = "2026-09-01"  # 92 days later
        body = dict(_VALID_BODY, from_date=from_d, until_date=until_d)
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(body)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("90", resp.json().get("error", ""))

    def test_range_exactly_90_days_is_allowed(self):
        """until_date − from_date == 90 days → OK (if court exists etc.)."""
        from_d = date(2026, 6, 1)
        until_d = from_d + timedelta(days=90)
        body = dict(
            _VALID_BODY,
            from_date=from_d.isoformat(),
            until_date=until_d.isoformat(),
        )

        # Provide enough mock responses for all GET and POST calls.
        # We'll use a side_effect that always returns no-overlap + slot created.
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        def post_side_effect(url, json=None, **kwargs):
            return _slot_created(0)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", side_effect=post_side_effect):
            resp = self._post(body)
        # Should be 200 (not 400) — 90-day range is allowed
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # AC7 — Happy path: slots generated for matching weekdays
    # ------------------------------------------------------------------

    def test_happy_path_creates_correct_number_of_slots(self):
        """
        Within 2026-06-01 (Mon) to 2026-06-07 (Sun), requesting mon+wed+fri
        should attempt to create 3 slots (Mon 01, Wed 03, Fri 05).
        """
        slot_counter = {"n": 0}

        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        def post_side_effect(url, json=None, **kwargs):
            n = slot_counter["n"]
            slot_counter["n"] += 1
            return _slot_created(n)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", side_effect=post_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["created"], 3)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(len(body["slots"]), 3)

    def test_response_shape(self):
        """Response must have created, skipped, and slots keys."""
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", return_value=_slot_created(0)):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("created", body)
        self.assertIn("skipped", body)
        self.assertIn("slots", body)
        self.assertIsInstance(body["slots"], list)

    def test_each_slot_has_expected_fields(self):
        """Each slot in the response must include id, court_id, start_at, end_at, status."""
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", return_value=_slot_created(0)):
            # Use a single-day range to get exactly one slot
            body = dict(_VALID_BODY, from_date="2026-06-01", until_date="2026-06-01")
            resp = self._post(body)

        self.assertEqual(resp.status_code, 200)
        slots = resp.json()["slots"]
        self.assertEqual(len(slots), 1)
        slot = slots[0]
        for field in ("id", "court_id", "start_at", "end_at", "status"):
            self.assertIn(field, slot)

    def test_single_day_range_with_matching_day(self):
        """from_date == until_date and it matches a requested day → 1 slot created."""
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", return_value=_slot_created(0)):
            body = dict(_VALID_BODY, days_of_week=["mon"], from_date="2026-06-01", until_date="2026-06-01")
            resp = self._post(body)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["created"], 1)

    def test_single_day_range_with_non_matching_day(self):
        """from_date == until_date but it does NOT match requested days → 0 created."""
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            # 2026-06-01 is Monday; requesting only Tue
            body = dict(_VALID_BODY, days_of_week=["tue"], from_date="2026-06-01", until_date="2026-06-01")
            resp = self._post(body)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["created"], 0)
        self.assertEqual(resp.json()["skipped"], 0)  # no matching days, nothing to skip

    # ------------------------------------------------------------------
    # AC8 — Overlapping slots silently skipped
    # ------------------------------------------------------------------

    def test_all_overlapping_returns_zero_created(self):
        """If every occurrence has an overlapping slot → created=0, skipped=3."""
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _overlap()  # every overlap check finds a conflict

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["created"], 0)
        self.assertEqual(body["skipped"], 3)
        self.assertEqual(body["slots"], [])

    def test_partial_overlap_mix(self):
        """First occurrence is free, second overlaps, third is free → created=2, skipped=1.

        Week: Mon=free, Wed=overlap, Fri=free
        """
        call_idx = {"n": 0}
        slot_idx = {"n": 0}

        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            # Alternate: no-overlap, overlap, no-overlap
            idx = call_idx["n"]
            call_idx["n"] += 1
            return _overlap() if idx == 1 else _no_overlap()

        def post_side_effect(url, json=None, **kwargs):
            n = slot_idx["n"]
            slot_idx["n"] += 1
            return _slot_created(n)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", side_effect=post_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["created"], 2)
        self.assertEqual(body["skipped"], 1)

    # ------------------------------------------------------------------
    # AC9 — Slots outside operating_hours skipped
    # ------------------------------------------------------------------

    def test_slot_outside_operating_hours_skipped(self):
        """
        Court only opens Mon–Fri 08:00–22:00; Sat–Sun not in hours.
        Request days_of_week=["sat"], start_time=10:00, end_time=12:00
        within a week that includes a Saturday → 0 created, 1 skipped.
        """
        court_weekdays_only = dict(_COURT_ROW, operating_hours={
            "mon": {"open": "08:00", "close": "22:00"},
            "tue": {"open": "08:00", "close": "22:00"},
            "wed": {"open": "08:00", "close": "22:00"},
            "thu": {"open": "08:00", "close": "22:00"},
            "fri": {"open": "08:00", "close": "22:00"},
        })

        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _mock_resp(200, [court_weekdays_only])
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            # 2026-06-06 is a Saturday
            body = dict(
                _VALID_BODY,
                days_of_week=["sat"],
                start_time="10:00",
                end_time="12:00",
                from_date="2026-06-06",
                until_date="2026-06-06",
            )
            resp = self._post(body)

        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_slot_time_before_opening_skipped(self):
        """
        Slot start_time before court opening → skipped.
        Court opens at 08:00 on Mon; requesting start_time=07:00.
        """
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            body = dict(
                _VALID_BODY,
                days_of_week=["mon"],
                start_time="07:00",
                end_time="09:00",
                from_date="2026-06-01",
                until_date="2026-06-01",
            )
            resp = self._post(body)

        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_slot_time_after_closing_skipped(self):
        """
        Slot end_time after court closing → skipped.
        Court closes at 22:00 on Mon; requesting end_time=23:00.
        """
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            body = dict(
                _VALID_BODY,
                days_of_week=["mon"],
                start_time="21:00",
                end_time="23:00",
                from_date="2026-06-01",
                until_date="2026-06-01",
            )
            resp = self._post(body)

        self.assertEqual(resp.status_code, 200)
        result = resp.json()
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["skipped"], 1)

    # ------------------------------------------------------------------
    # AC10 — 200 even when created == 0
    # ------------------------------------------------------------------

    def test_zero_created_still_returns_200(self):
        """All slots skipped → still 200 (not 409)."""
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_slot_insert_failure_still_counts_others(self):
        """
        If a slot insert fails (503 from Supabase), that occurrence is skipped.
        """
        call_idx = {"n": 0}

        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        def post_side_effect(url, json=None, **kwargs):
            idx = call_idx["n"]
            call_idx["n"] += 1
            if idx == 1:
                return _mock_resp(500, {"error": "DB error"})
            return _slot_created(idx)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", side_effect=post_side_effect):
            resp = self._post(_VALID_BODY)

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        # Mon (ok) + Wed (fail → skip) + Fri (ok) → 2 created, 1 skipped
        self.assertEqual(body["created"], 2)
        self.assertEqual(body["skipped"], 1)

    def test_from_date_equals_until_date_is_valid(self):
        """Single-day range is valid (from_date == until_date)."""
        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", return_value=_slot_created(0)):
            body = dict(_VALID_BODY, from_date="2026-06-01", until_date="2026-06-01", days_of_week=["mon"])
            resp = self._post(body)
        self.assertEqual(resp.status_code, 200)

    def test_get_method_returns_405(self):
        """GET /api/courts/{id}/recurrence → 405."""
        resp = self.client.get(self.url, HTTP_AUTHORIZATION="Bearer token")
        self.assertEqual(resp.status_code, 405)

    def test_slots_sent_to_supabase_have_correct_datetime(self):
        """
        Verifies the slots inserted into Supabase have the correct start_at/end_at
        based on from_date and start_time/end_time (UTC timestamps).
        """
        captured_posts = []

        def get_side_effect(url, params=None, **kwargs):
            if "courts" in url:
                return _court_resp()
            return _no_overlap()

        def post_side_effect(url, json=None, **kwargs):
            captured_posts.append(json or {})
            return _slot_created(len(captured_posts) - 1)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=get_side_effect), \
             patch("courts.views.requests.post", side_effect=post_side_effect):
            # Only Monday in range 2026-06-01
            body = dict(_VALID_BODY, days_of_week=["mon"], from_date="2026-06-01", until_date="2026-06-01")
            resp = self._post(body)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(captured_posts), 1)
        inserted = captured_posts[0]
        # start_at should be 2026-06-01T09:00:00+00:00 (UTC)
        self.assertIn("2026-06-01", inserted["start_at"])
        self.assertIn("09:00", inserted["start_at"])
        self.assertIn("11:00", inserted["end_at"])
        self.assertEqual(inserted["court_id"], _COURT_ID)
        self.assertEqual(inserted["status"], "open")
