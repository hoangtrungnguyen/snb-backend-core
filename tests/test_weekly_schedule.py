"""
Tests for grava-3106.5 — Weekly schedule & slot detail queries.

Endpoints covered:
  grava-3106.5.1  GET /api/courts/{id}/slots?from=DATE&to=DATE
  grava-3106.5.2  GET /api/sports-centers/{id}/schedule?date=DATE
  grava-3106.5.3  GET /api/slots/{id}  — slot detail
  grava-3106.5.4  Response includes status, booking_id (if booked), blocked_reason (if blocked)

All Supabase HTTP calls are mocked — no real network requests.
"""
import json
from unittest.mock import patch, MagicMock

from django.test import TestCase, Client

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_COURT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_COURT_ID_2 = "bbbbbbbb-0000-0000-0000-000000000009"
_SLOT_ID = "cccccccc-0000-0000-0000-000000000003"
_BOOKING_ID = "dddddddd-0000-0000-0000-000000000004"
_SC_ID = "eeeeeeee-0000-0000-0000-000000000005"  # sports center id

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Court Alpha",
    "slug": "court-alpha",
    "sport_types": ["badminton"],
    "capacity": 4,
    "price_per_hour": 100000,
    "operating_hours": {
        "mon": {"open": "08:00", "close": "22:00"},
        "tue": {"open": "08:00", "close": "22:00"},
        "wed": {"open": "08:00", "close": "22:00"},
    },
    "address": "123 Main St",
    "lat": 10.0,
    "lng": 106.0,
    "status": "active",
    "amenities": [],
    "description": None,
    "photos": [],
    "created_at": "2026-05-01T00:00:00Z",
    "updated_at": "2026-05-01T00:00:00Z",
    "sports_center_id": _SC_ID,
}

_COURT_ROW_2 = dict(_COURT_ROW, id=_COURT_ID_2, name="Court Beta", slug="court-beta")

_OPEN_SLOT_ROW = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": "2026-05-25T10:00:00Z",
    "end_at": "2026-05-25T12:00:00Z",
    "status": "open",
    "is_owner_slot": False,
    "access_policy": "open",
    "max_players": 4,
    "blocked_reason": None,
    "booking_id": None,
    "notes": None,
    "created_at": "2026-05-01T00:00:00Z",
    "updated_at": "2026-05-01T00:00:00Z",
}

_BOOKED_SLOT_ROW = dict(_OPEN_SLOT_ROW,
    status="booked",
    booking_id=_BOOKING_ID,
)

_BLOCKED_SLOT_ROW = dict(_OPEN_SLOT_ROW,
    status="blocked",
    blocked_reason="Court maintenance",
    booking_id=None,
)


def _mock_resp(status_code, data):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


# ---------------------------------------------------------------------------
# grava-3106.5.1  GET /api/courts/{id}/slots?from=DATE&to=DATE
# ---------------------------------------------------------------------------

class CourtSlotsRangeTests(TestCase):
    """Tests for GET /api/courts/{id}/slots?from=DATE&to=DATE (grava-3106.5.1)."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/courts/{_COURT_ID}/slots"

    def _get(self, params=None):
        return self.client.get(self.url, params or {})

    # --- Happy path ---

    def test_returns_slots_in_range(self):
        """Returns 200 with all slots between from and to dates."""
        slot_rows = [_OPEN_SLOT_ROW]
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),   # court lookup
                _mock_resp(200, slot_rows),       # slots query
            ]
            resp = self._get({"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 1)

    def test_slot_includes_required_fields(self):
        """Each slot in results has at minimum: id, start_at, end_at, status."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, [_OPEN_SLOT_ROW]),
            ]
            resp = self._get({"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 200)
        slot = resp.json()["results"][0]
        for field in ("id", "court_id", "start_at", "end_at", "status"):
            self.assertIn(field, slot, f"Missing field: {field}")

    def test_slot_includes_booking_id_when_booked(self):
        """Booked slot includes booking_id in response (grava-3106.5.4)."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, [_BOOKED_SLOT_ROW]),
            ]
            resp = self._get({"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 200)
        slot = resp.json()["results"][0]
        self.assertEqual(slot["status"], "booked")
        self.assertEqual(slot["booking_id"], _BOOKING_ID)

    def test_slot_includes_blocked_reason_when_blocked(self):
        """Blocked slot includes blocked_reason (grava-3106.5.4)."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, [_BLOCKED_SLOT_ROW]),
            ]
            resp = self._get({"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 200)
        slot = resp.json()["results"][0]
        self.assertEqual(slot["status"], "blocked")
        self.assertEqual(slot["blocked_reason"], "Court maintenance")

    def test_empty_range_returns_empty_results(self):
        """No slots in range → results is an empty list."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, []),
            ]
            resp = self._get({"from": "2026-05-01", "to": "2026-05-02"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"], [])

    def test_query_filters_by_from_and_to(self):
        """Supabase query is called with gte.from and lt.to filters."""
        captured = {}

        def capture_get(url, params=None, **kwargs):
            if "slots" in url:
                captured.update(params or {})
                return _mock_resp(200, [])
            return _mock_resp(200, [_COURT_ROW])

        with patch("courts.views.requests.get", side_effect=capture_get):
            self._get({"from": "2026-05-25", "to": "2026-05-27"})

        # start_at must be >= from date and < to date (or similar range filter)
        self.assertTrue(
            any("2026-05-25" in str(v) for v in captured.values()),
            f"Expected from-date filter in Supabase params, got: {captured}",
        )

    def test_multiple_slots_returned(self):
        """Multiple slots in range are all returned."""
        slot2 = dict(_OPEN_SLOT_ROW, id="slot-2", start_at="2026-05-25T14:00:00Z", end_at="2026-05-25T16:00:00Z")
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, [_OPEN_SLOT_ROW, slot2]),
            ]
            resp = self._get({"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["results"]), 2)

    # --- Validation ---

    def test_missing_from_returns_400(self):
        """Missing from param → 400."""
        resp = self._get({"to": "2026-05-26"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_to_returns_400(self):
        """Missing to param → 400."""
        resp = self._get({"from": "2026-05-25"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_from_date_returns_400(self):
        """Invalid from date format → 400."""
        resp = self._get({"from": "not-a-date", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_to_date_returns_400(self):
        """Invalid to date format → 400."""
        resp = self._get({"from": "2026-05-25", "to": "not-a-date"})
        self.assertEqual(resp.status_code, 400)

    def test_court_not_found_returns_404(self):
        """Court not found → 404."""
        with patch("courts.views.requests.get", return_value=_mock_resp(200, [])):
            resp = self._get({"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 404)

    def test_service_unavailable_returns_503(self):
        """Supabase down → 503."""
        import requests as req_lib
        with patch("courts.views.requests.get", side_effect=req_lib.RequestException("down")):
            resp = self._get({"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 503)

    def test_get_method_allowed_no_auth_required(self):
        """GET is public — no auth header needed."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, []),
            ]
            resp = self.client.get(self.url, {"from": "2026-05-25", "to": "2026-05-26"})
        self.assertEqual(resp.status_code, 200)


# ---------------------------------------------------------------------------
# grava-3106.5.2  GET /api/sports-centers/{id}/schedule?date=DATE
# ---------------------------------------------------------------------------

class SportsCenterScheduleTests(TestCase):
    """Tests for GET /api/sports-centers/{id}/schedule?date=DATE (grava-3106.5.2)."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/sports-centers/{_SC_ID}/schedule"

    def _get(self, params=None):
        return self.client.get(self.url, params or {})

    # --- Happy path ---

    def test_returns_courts_with_slots_for_date(self):
        """Returns 200 with courts list, each having their slots for the day."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW, _COURT_ROW_2]),  # courts for sports center
                _mock_resp(200, [_OPEN_SLOT_ROW]),              # slots for court 1
                _mock_resp(200, []),                            # slots for court 2
            ]
            resp = self._get({"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("date", data)
        self.assertIn("courts", data)
        self.assertEqual(len(data["courts"]), 2)

    def test_court_entry_includes_slots_array(self):
        """Each court object has a slots array."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, [_OPEN_SLOT_ROW]),
            ]
            resp = self._get({"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 200)
        court = resp.json()["courts"][0]
        self.assertIn("slots", court)
        self.assertIsInstance(court["slots"], list)

    def test_court_entry_includes_basic_court_info(self):
        """Each court object has id, name, and status."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, []),
            ]
            resp = self._get({"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 200)
        court = resp.json()["courts"][0]
        for field in ("id", "name", "status"):
            self.assertIn(field, court, f"Missing field: {field}")

    def test_slot_includes_booking_id_and_blocked_reason(self):
        """Slots in schedule response include booking_id and blocked_reason (grava-3106.5.4)."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, [_BOOKED_SLOT_ROW]),
            ]
            resp = self._get({"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 200)
        slot = resp.json()["courts"][0]["slots"][0]
        self.assertIn("booking_id", slot)
        self.assertIn("blocked_reason", slot)
        self.assertEqual(slot["booking_id"], _BOOKING_ID)

    def test_response_includes_date_field(self):
        """Response echoes the requested date."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, []),
            ]
            resp = self._get({"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["date"], "2026-05-25")

    def test_no_courts_returns_empty_list(self):
        """Sports center with no courts → courts is empty list."""
        with patch("courts.views.requests.get", return_value=_mock_resp(200, [])):
            resp = self._get({"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["courts"], [])

    def test_public_endpoint_no_auth_required(self):
        """No auth needed."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_COURT_ROW]),
                _mock_resp(200, []),
            ]
            resp = self.client.get(self.url, {"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 200)

    # --- Validation ---

    def test_missing_date_returns_400(self):
        """Missing date param → 400."""
        resp = self._get()
        self.assertEqual(resp.status_code, 400)

    def test_invalid_date_format_returns_400(self):
        """Invalid date format → 400."""
        resp = self._get({"date": "not-a-date"})
        self.assertEqual(resp.status_code, 400)

    def test_service_unavailable_returns_503(self):
        """Supabase down → 503."""
        import requests as req_lib
        with patch("courts.views.requests.get", side_effect=req_lib.RequestException("down")):
            resp = self._get({"date": "2026-05-25"})
        self.assertEqual(resp.status_code, 503)


# ---------------------------------------------------------------------------
# grava-3106.5.3  GET /api/slots/{id}  — slot detail
# ---------------------------------------------------------------------------

class SlotDetailTests(TestCase):
    """Tests for GET /api/slots/{id} (grava-3106.5.3)."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/slots/{_SLOT_ID}"

    def _get(self):
        return self.client.get(self.url)

    # --- Happy path ---

    def test_returns_open_slot_detail(self):
        """Returns 200 with open slot detail."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_OPEN_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["id"], _SLOT_ID)

    def test_response_shape_has_all_required_fields(self):
        """
        Slot detail response must include:
        id, court_id, court_name, start_at, end_at, duration_minutes,
        status, access_policy, max_players, blocked_reason, booking_id, notes
        (grava-3106.5.3)
        """
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_OPEN_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        required_fields = (
            "id", "court_id", "court_name", "start_at", "end_at",
            "duration_minutes", "status", "access_policy", "max_players",
            "blocked_reason", "booking_id", "notes",
        )
        for field in required_fields:
            self.assertIn(field, data, f"Missing required field: {field}")

    def test_duration_minutes_computed_correctly(self):
        """duration_minutes is (end_at - start_at) in whole minutes."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_OPEN_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        # 10:00 → 12:00 = 120 minutes
        self.assertEqual(resp.json()["duration_minutes"], 120)

    def test_court_name_included_in_response(self):
        """court_name is pulled from the related court row."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_OPEN_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["court_name"], "Court Alpha")

    def test_booked_slot_includes_booking_id(self):
        """Booked slot → booking_id is set (grava-3106.5.4)."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_BOOKED_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "booked")
        self.assertEqual(data["booking_id"], _BOOKING_ID)

    def test_blocked_slot_includes_blocked_reason(self):
        """Blocked slot → blocked_reason is set (grava-3106.5.4)."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_BLOCKED_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "blocked")
        self.assertEqual(data["blocked_reason"], "Court maintenance")

    def test_open_slot_has_null_booking_id_and_blocked_reason(self):
        """Open slot → booking_id=null, blocked_reason=null (grava-3106.5.4)."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_OPEN_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["booking_id"])
        self.assertIsNone(data["blocked_reason"])

    def test_public_endpoint_no_auth_required(self):
        """No auth header needed."""
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _mock_resp(200, [_OPEN_SLOT_ROW]),
                _mock_resp(200, [_COURT_ROW]),
            ]
            resp = self._get()
        self.assertEqual(resp.status_code, 200)

    # --- Error cases ---

    def test_slot_not_found_returns_404(self):
        """Slot not found → 404."""
        with patch("courts.views.requests.get", return_value=_mock_resp(200, [])):
            resp = self._get()
        self.assertEqual(resp.status_code, 404)

    def test_service_unavailable_returns_503(self):
        """Supabase down → 503."""
        import requests as req_lib
        with patch("courts.views.requests.get", side_effect=req_lib.RequestException("down")):
            resp = self._get()
        self.assertEqual(resp.status_code, 503)

    def test_court_fetch_failure_returns_503(self):
        """Court fetch failure after slot found → 503."""
        import requests as req_lib

        def side(url, **kwargs):
            if "slots" in url:
                return _mock_resp(200, [_OPEN_SLOT_ROW])
            raise req_lib.RequestException("court down")

        with patch("courts.views.requests.get", side_effect=side):
            resp = self._get()
        self.assertEqual(resp.status_code, 503)
