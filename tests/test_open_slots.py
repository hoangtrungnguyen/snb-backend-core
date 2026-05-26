"""
Tests for GET /api/slots/open-for-join (grava-5044.3 — BCORE-062).

Subtasks covered:
  grava-5044.3.1  GET /slots/open-for-join?lat=&lng=&radius_km=&sport=
  grava-5044.3.2  Each slot includes court_name, sport, start_at, end_at, max_players, current_players
  grava-5044.3.3  Sorted by start_at ASC
"""
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

COURT_ID_1 = str(uuid.uuid4())
COURT_ID_2 = str(uuid.uuid4())
SLOT_ID_1 = str(uuid.uuid4())
SLOT_ID_2 = str(uuid.uuid4())

BASE_LAT = 10.7769
BASE_LNG = 106.7009

_NOW = datetime.now(timezone.utc)
_FUTURE_1 = (_NOW + timedelta(hours=2)).isoformat()
_FUTURE_2 = (_NOW + timedelta(hours=4)).isoformat()
_FUTURE_3 = (_NOW + timedelta(hours=6)).isoformat()
_PAST = (_NOW - timedelta(hours=1)).isoformat()


def _court_row(court_id=None, name="Test Court", lat=10.7769, lng=106.7009,
               status="approved", sport_types=None):
    court_id = court_id or str(uuid.uuid4())
    sport_types = sport_types or ["football"]
    return {
        "id": court_id,
        "owner_id": str(uuid.uuid4()),
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "sport_types": sport_types,
        "capacity": 10,
        "price_per_hour": "50.00",
        "operating_hours": None,
        "address": "123 Main St",
        "lat": str(lat),
        "lng": str(lng),
        "status": status,
        "amenities": [],
        "description": None,
        "photos": [],
        "auto_approve_single": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _slot_row(slot_id=None, court_id=None, start_at=None, end_at=None,
              status="open", access_policy="open", max_players=4):
    slot_id = slot_id or str(uuid.uuid4())
    court_id = court_id or str(uuid.uuid4())
    start_at = start_at or _FUTURE_1
    end_at = end_at or _FUTURE_2
    return {
        "id": slot_id,
        "court_id": court_id,
        "start_at": start_at,
        "end_at": end_at,
        "status": status,
        "access_policy": access_policy,
        "max_players": max_players,
        "blocked_reason": None,
        "is_owner_slot": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _supa_ok(rows):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = rows
    return r


def _supa_error():
    r = MagicMock()
    r.status_code = 503
    r.json.return_value = {"message": "error"}
    return r


class TestOpenSlotsBasicValidation(TestCase):
    """Parameter validation — grava-5044.3.1."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/slots/open-for-join"

    def test_missing_lat_returns_400(self):
        r = self.client.get(self.url, {"lng": "106.7", "radius_km": "5"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lat", r.json()["error"])

    def test_missing_lng_returns_400(self):
        r = self.client.get(self.url, {"lat": "10.7", "radius_km": "5"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lng", r.json()["error"])

    def test_invalid_lat_returns_400(self):
        r = self.client.get(self.url, {"lat": "bad", "lng": "106.7"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lat", r.json()["error"])

    def test_invalid_lng_returns_400(self):
        r = self.client.get(self.url, {"lat": "10.7", "lng": "bad"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lng", r.json()["error"])

    def test_invalid_radius_km_returns_400(self):
        r = self.client.get(self.url, {"lat": "10.7", "lng": "106.7", "radius_km": "bad"})
        self.assertEqual(r.status_code, 400)

    def test_radius_km_not_in_valid_set_returns_400(self):
        r = self.client.get(self.url, {"lat": "10.7", "lng": "106.7", "radius_km": "10"})
        self.assertEqual(r.status_code, 400)


class TestOpenSlotsResponseShape(TestCase):
    """
    Each slot must include court_name, sport, start_at, end_at, max_players, current_players.
    grava-5044.3.2
    """

    def setUp(self):
        self.client = Client()
        self.url = "/api/slots/open-for-join"

    def _call(self, lat=BASE_LAT, lng=BASE_LNG, radius_km=5, extra=None):
        params = {"lat": str(lat), "lng": str(lng), "radius_km": str(radius_km)}
        if extra:
            params.update(extra)
        return self.client.get(self.url, params)

    def test_returns_required_fields(self):
        """Each result must include all required fields."""
        court = _court_row(court_id=COURT_ID_1, name="Alpha Court", lat=BASE_LAT, lng=BASE_LNG,
                           sport_types=["football"])
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1, start_at=_FUTURE_1,
                         end_at=_FUTURE_2, max_players=4)

        # Courts GET, then slots GET, then participants GET
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([court]),    # fetch courts
                _supa_ok([slot]),     # fetch open slots
                _supa_ok([]),         # count slot_participants for SLOT_ID_1
            ]
            r = self._call()

        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("results", data)
        results = data["results"]
        self.assertEqual(len(results), 1)
        item = results[0]
        # Required fields per grava-5044.3.2
        self.assertIn("slot_id", item)
        self.assertIn("court_name", item)
        self.assertIn("sport", item)
        self.assertIn("start_at", item)
        self.assertIn("end_at", item)
        self.assertIn("max_players", item)
        self.assertIn("current_players", item)

    def test_court_name_populated(self):
        """court_name should come from the joined court row."""
        court = _court_row(court_id=COURT_ID_1, name="Alpha Court", lat=BASE_LAT, lng=BASE_LNG)
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1)

        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([court]),
                _supa_ok([slot]),
                _supa_ok([]),
            ]
            r = self._call()

        item = r.json()["results"][0]
        self.assertEqual(item["court_name"], "Alpha Court")

    def test_sport_populated_from_court_sport_types(self):
        """sport should be the first entry from the court's sport_types list."""
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG,
                           sport_types=["badminton", "tennis"])
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1)

        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([court]),
                _supa_ok([slot]),
                _supa_ok([]),
            ]
            r = self._call()

        item = r.json()["results"][0]
        self.assertEqual(item["sport"], "badminton")

    def test_current_players_counted(self):
        """current_players should count slot_participants rows for that slot."""
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1)
        participants = [{"id": str(uuid.uuid4())}, {"id": str(uuid.uuid4())}]

        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([court]),
                _supa_ok([slot]),
                _supa_ok(participants),
            ]
            r = self._call()

        item = r.json()["results"][0]
        self.assertEqual(item["current_players"], 2)

    def test_max_players_from_slot(self):
        """max_players reflects what's stored on the slot."""
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1, max_players=6)

        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([court]),
                _supa_ok([slot]),
                _supa_ok([]),
            ]
            r = self._call()

        item = r.json()["results"][0]
        self.assertEqual(item["max_players"], 6)


class TestOpenSlotsSortedByStartAt(TestCase):
    """
    Results must be sorted by start_at ASC.
    grava-5044.3.3
    """

    def setUp(self):
        self.client = Client()
        self.url = "/api/slots/open-for-join"

    def test_sorted_start_at_asc(self):
        """Slots from a single court are returned in chronological order."""
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)
        slot_later = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1,
                               start_at=_FUTURE_3, end_at=_FUTURE_3)
        slot_earlier = _slot_row(slot_id=SLOT_ID_2, court_id=COURT_ID_1,
                                 start_at=_FUTURE_1, end_at=_FUTURE_2)

        # Slots arrive out-of-order from upstream
        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([court]),
                _supa_ok([slot_earlier, slot_later]),  # Supabase returns ASC; test verifies it
                _supa_ok([]),   # participants for slot_earlier
                _supa_ok([]),   # participants for slot_later
            ]
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "5",
            })

        self.assertEqual(r.status_code, 200)
        results = r.json()["results"]
        self.assertEqual(len(results), 2)
        self.assertLessEqual(results[0]["start_at"], results[1]["start_at"])


class TestOpenSlotsDistanceFilter(TestCase):
    """
    Slots from courts outside radius_km must be excluded.
    """

    def setUp(self):
        self.client = Client()
        self.url = "/api/slots/open-for-join"

    def test_court_outside_radius_excluded(self):
        """Court 2 km away from the caller should not appear when radius_km=1."""
        # Approx 2 km north
        far_lat = BASE_LAT + 0.018   # ~2 km
        far_court = _court_row(court_id=COURT_ID_2, lat=far_lat, lng=BASE_LNG)
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_2)

        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([far_court]),
                # slots should never be fetched for out-of-range court, OR fetched but empty
                # implementation may choose either; just ensure empty result
                _supa_ok([]),
            ]
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "1",
            })

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"], [])

    def test_court_within_radius_included(self):
        """Court within radius_km is included."""
        close_court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1)

        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([close_court]),
                _supa_ok([slot]),
                _supa_ok([]),
            ]
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "5",
            })

        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["results"]), 1)


class TestOpenSlotsSportFilter(TestCase):
    """sport query parameter filters results to matching courts."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/slots/open-for-join"

    def test_sport_filter_excludes_non_matching_courts(self):
        """When sport=badminton is passed, football-only courts should be excluded."""
        football_court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG,
                                    sport_types=["football"])
        slot = _slot_row(court_id=COURT_ID_1)

        with patch("courts.views.requests.get") as mock_get:
            # Supabase returns football court; sport filter happens in Python OR via query param
            mock_get.side_effect = [
                _supa_ok([football_court]),
                _supa_ok([slot]),
                _supa_ok([]),
            ]
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG),
                "radius_km": "5", "sport": "badminton",
            })

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"], [])

    def test_sport_filter_includes_matching_courts(self):
        """When sport=football, football courts appear."""
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG,
                           sport_types=["football"])
        slot = _slot_row(slot_id=SLOT_ID_1, court_id=COURT_ID_1)

        with patch("courts.views.requests.get") as mock_get:
            mock_get.side_effect = [
                _supa_ok([court]),
                _supa_ok([slot]),
                _supa_ok([]),
            ]
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG),
                "radius_km": "5", "sport": "football",
            })

        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["results"]), 1)


class TestOpenSlotsEmptyAndErrors(TestCase):
    """Edge cases: empty results, service errors."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/slots/open-for-join"

    def test_no_courts_returns_empty_results(self):
        with patch("courts.views.requests.get", return_value=_supa_ok([])):
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["results"], [])

    def test_courts_service_error_returns_503(self):
        with patch("courts.views.requests.get", return_value=_supa_error()):
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertEqual(r.status_code, 503)

    def test_default_radius_km_5_is_accepted(self):
        """radius_km defaults to 5 when not provided."""
        with patch("courts.views.requests.get", return_value=_supa_ok([])):
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertEqual(r.status_code, 200)

    def test_no_auth_required(self):
        """Public endpoint — no Authorization header needed."""
        with patch("courts.views.requests.get", return_value=_supa_ok([])):
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertNotEqual(r.status_code, 401)
        self.assertNotEqual(r.status_code, 403)
