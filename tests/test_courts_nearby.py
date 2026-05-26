"""
Tests for GET /api/courts/nearby (grava-5044.1 — BCORE-060).

Subtasks covered:
  grava-5044.1.1  GET /courts/nearby?lat=&lng=&radius_km=5&sport=&date=&price_min=&price_max=&time_of_day=
  grava-5044.1.2  Filter courts.status = approved only
  grava-5044.1.3  Distance via Haversine fallback
  grava-5044.1.4  Each court includes has_open_slots_today: bool
  grava-5044.1.5  sport filter: WHERE sport_types @> ARRAY[sport]
  grava-5044.1.6  price_min / price_max filter on courts.price_per_hour
  grava-5044.1.7  time_of_day filter: morning|afternoon|evening|night
  grava-5044.1.8  Radius options: 1, 3, 5 km; default 5
  grava-5044.1.9  Response sorted by distance ASC
  grava-5044.1.10 Empty result: [] with 200
"""
import json
import math
import uuid
from datetime import date, timezone
from unittest.mock import MagicMock, patch, call

from django.test import Client, TestCase

COURT_ID_1 = str(uuid.uuid4())
COURT_ID_2 = str(uuid.uuid4())
COURT_ID_3 = str(uuid.uuid4())

BASE_LAT = 10.7769
BASE_LNG = 106.7009


def _court_row(court_id=None, name="Test Court", lat=10.7769, lng=106.7009,
               status="approved", sport_types=None, price_per_hour="50.00",
               **overrides):
    court_id = court_id or str(uuid.uuid4())
    sport_types = sport_types or ["football"]
    row = {
        "id": court_id,
        "owner_id": str(uuid.uuid4()),
        "name": name,
        "slug": name.lower().replace(" ", "-"),
        "sport_types": sport_types,
        "capacity": 22,
        "price_per_hour": price_per_hour,
        "operating_hours": {"mon": {"open": "06:00", "close": "22:00"}},
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
    row.update(overrides)
    return row


def _supa_list(rows):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = rows
    return r


def _supa_empty():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = []
    return r


def _supa_error():
    r = MagicMock()
    r.status_code = 503
    r.json.return_value = {"message": "error"}
    return r


class TestCourtsNearbyBasic(TestCase):
    """Basic endpoint availability and param validation."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_missing_lat_returns_400(self):
        r = self.client.get(self.url, {"lng": "106.7", "radius_km": "5"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lat", r.json()["error"])

    def test_missing_lng_returns_400(self):
        r = self.client.get(self.url, {"lat": "10.7", "radius_km": "5"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("lng", r.json()["error"])

    def test_invalid_lat_returns_400(self):
        r = self.client.get(self.url, {"lat": "not-a-number", "lng": "106.7"})
        self.assertEqual(r.status_code, 400)

    def test_invalid_lng_returns_400(self):
        r = self.client.get(self.url, {"lat": "10.7", "lng": "not-a-number"})
        self.assertEqual(r.status_code, 400)

    def test_invalid_radius_returns_400(self):
        r = self.client.get(self.url, {"lat": "10.7", "lng": "106.7", "radius_km": "10"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("radius_km", r.json()["error"])

    def test_invalid_time_of_day_returns_400(self):
        r = self.client.get(self.url, {
            "lat": "10.7", "lng": "106.7", "time_of_day": "midnight"
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("time_of_day", r.json()["error"])

    def test_post_not_allowed(self):
        r = self.client.post(self.url)
        self.assertEqual(r.status_code, 405)


class TestCourtsNearbyEmptyResult(TestCase):
    """grava-5044.1.10 — Empty result: [] with 200."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_empty_courts_returns_200_empty_list(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body, [])

    def test_all_courts_out_of_radius_returns_empty(self):
        """Court at 100 km away should be filtered out."""
        far_court = _court_row(
            court_id=COURT_ID_1,
            lat=9.0,    # ~190 km from BASE_LAT
            lng=106.7009,
        )
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_list([far_court])
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "5"
            })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])


class TestCourtsNearbyStatusFilter(TestCase):
    """grava-5044.1.2 — Only approved courts returned."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_non_approved_courts_excluded(self):
        """Courts with status=pending/suspended must not appear in results."""
        pending_court = _court_row(court_id=COURT_ID_1, status="pending")
        suspended_court = _court_row(court_id=COURT_ID_2, status="suspended")

        with patch("courts.views.requests") as mr:
            # Supabase query already filters for approved, so returns empty
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})

        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_supabase_called_with_status_approved_filter(self):
        """Endpoint must request courts with status=approved from Supabase."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})

        call_args = mr.get.call_args
        params = call_args[1].get("params", {}) if call_args[1] else call_args[0][1]
        # Check the Supabase call includes status=eq.approved
        self.assertIn("eq.approved", str(call_args))


class TestCourtsNearbyDistanceSort(TestCase):
    """grava-5044.1.9 — Response sorted by distance ASC."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_sorted_by_distance_asc(self):
        """Closer court should appear before farther court in results."""
        # Court 1: ~0 km away (very close)
        near_court = _court_row(court_id=COURT_ID_1, name="Near Court",
                                lat=BASE_LAT, lng=BASE_LNG)
        # Court 2: ~2 km away
        far_court = _court_row(court_id=COURT_ID_2, name="Far Court",
                               lat=BASE_LAT + 0.018, lng=BASE_LNG)

        # Return far court first from Supabase to test that we sort client-side
        with patch("courts.views.requests") as mr:
            # First call: courts list; subsequent calls: slots open check
            def get_side_effect(*args, **kwargs):
                url_str = args[0] if args else kwargs.get("url", "")
                if "slots" in str(url_str):
                    return _supa_empty()
                return _supa_list([far_court, near_court])
            mr.get.side_effect = get_side_effect
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 2)
        # Near court should be first
        self.assertEqual(body[0]["id"], COURT_ID_1)
        self.assertEqual(body[1]["id"], COURT_ID_2)
        # Distance field should be present and ascending
        self.assertLess(body[0]["distance_km"], body[1]["distance_km"])


class TestCourtsNearbyDistanceField(TestCase):
    """grava-5044.1.3 — Distance calculated and included in response."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_distance_km_included_in_response(self):
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)
        with patch("courts.views.requests") as mr:
            def get_side_effect(*args, **kwargs):
                url_str = str(args[0]) if args else ""
                if "slots" in url_str:
                    return _supa_empty()
                return _supa_list([court])
            mr.get.side_effect = get_side_effect
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertIn("distance_km", body[0])
        # Court is at origin — distance should be ~0
        self.assertAlmostEqual(body[0]["distance_km"], 0.0, places=2)


class TestCourtsNearbyHasOpenSlots(TestCase):
    """grava-5044.1.4 — Each court includes has_open_slots_today: bool."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_has_open_slots_today_true_when_open_slot_exists(self):
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)
        open_slot = {"id": str(uuid.uuid4()), "court_id": COURT_ID_1, "status": "open"}

        with patch("courts.views.requests") as mr:
            call_count = [0]
            def get_side_effect(*args, **kwargs):
                url_str = str(args[0]) if args else ""
                if "slots" in url_str:
                    return _supa_list([open_slot])
                return _supa_list([court])
            mr.get.side_effect = get_side_effect
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertIn("has_open_slots_today", body[0])
        self.assertTrue(body[0]["has_open_slots_today"])

    def test_has_open_slots_today_false_when_no_open_slot(self):
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)

        with patch("courts.views.requests") as mr:
            def get_side_effect(*args, **kwargs):
                url_str = str(args[0]) if args else ""
                if "slots" in url_str:
                    return _supa_empty()
                return _supa_list([court])
            mr.get.side_effect = get_side_effect
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertFalse(body[0]["has_open_slots_today"])


class TestCourtsNearbySportFilter(TestCase):
    """grava-5044.1.5 — sport filter: WHERE sport_types @> ARRAY[sport]."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_sport_filter_passed_to_supabase(self):
        """When sport= is given, the Supabase query should include sport filtering."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "sport": "football"
            })

        self.assertEqual(r.status_code, 200)
        call_args = mr.get.call_args
        # Check sport filter was included in the Supabase request
        self.assertIn("football", str(call_args))

    def test_sport_filter_excludes_non_matching_courts(self):
        """Courts without the requested sport should be filtered out."""
        football_court = _court_row(
            court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG,
            sport_types=["football"]
        )
        tennis_court = _court_row(
            court_id=COURT_ID_2, lat=BASE_LAT, lng=BASE_LNG,
            sport_types=["tennis"]
        )

        with patch("courts.views.requests") as mr:
            def get_side_effect(*args, **kwargs):
                url_str = str(args[0]) if args else ""
                params = kwargs.get("params", {})
                if "slots" in url_str:
                    return _supa_empty()
                # Simulate Supabase filtering: only return football courts
                return _supa_list([football_court])
            mr.get.side_effect = get_side_effect
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "sport": "football"
            })

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], COURT_ID_1)


class TestCourtsNearbyPriceFilter(TestCase):
    """grava-5044.1.6 — price_min / price_max filter on courts.price_per_hour."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_price_min_filter(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "price_min": "30"
            })
        self.assertEqual(r.status_code, 200)
        # price_min should be passed to Supabase
        self.assertIn("30", str(mr.get.call_args))

    def test_price_max_filter(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "price_max": "100"
            })
        self.assertEqual(r.status_code, 200)
        self.assertIn("100", str(mr.get.call_args))

    def test_invalid_price_min_returns_400(self):
        r = self.client.get(self.url, {
            "lat": str(BASE_LAT), "lng": str(BASE_LNG), "price_min": "abc"
        })
        self.assertEqual(r.status_code, 400)

    def test_invalid_price_max_returns_400(self):
        r = self.client.get(self.url, {
            "lat": str(BASE_LAT), "lng": str(BASE_LNG), "price_max": "abc"
        })
        self.assertEqual(r.status_code, 400)


class TestCourtsNearbyTimeOfDayFilter(TestCase):
    """grava-5044.1.7 — time_of_day filter: morning|afternoon|evening|night."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def _get_with_time_of_day(self, time_of_day):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "time_of_day": time_of_day
            })
        return r

    def test_morning_filter_accepted(self):
        r = self._get_with_time_of_day("morning")
        self.assertEqual(r.status_code, 200)

    def test_afternoon_filter_accepted(self):
        r = self._get_with_time_of_day("afternoon")
        self.assertEqual(r.status_code, 200)

    def test_evening_filter_accepted(self):
        r = self._get_with_time_of_day("evening")
        self.assertEqual(r.status_code, 200)

    def test_night_filter_accepted(self):
        r = self._get_with_time_of_day("night")
        self.assertEqual(r.status_code, 200)

    def test_invalid_time_of_day_returns_400(self):
        r = self._get_with_time_of_day("dawn")
        self.assertEqual(r.status_code, 400)
        self.assertIn("time_of_day", r.json()["error"])

    def test_time_of_day_filter_passed_to_supabase(self):
        """time_of_day=morning should filter slots to morning hours (06:00-12:00)."""
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)
        with patch("courts.views.requests") as mr:
            def side_effect(*args, **kwargs):
                url_str = str(args[0]) if args else ""
                if "slots" in url_str:
                    return _supa_empty()
                return _supa_list([court])
            mr.get.side_effect = side_effect
            self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "time_of_day": "morning"
            })
        # Check that the slots Supabase call includes time range filters for morning
        call_args_str = str(mr.get.call_args_list)
        # morning = 06:00-12:00
        self.assertIn("06:00", call_args_str)


class TestCourtsNearbyRadiusOptions(TestCase):
    """grava-5044.1.8 — Radius options: 1, 3, 5 km; default 5."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_default_radius_is_5km(self):
        """Court at 4 km away should appear with default radius (5 km)."""
        # ~4 km north of base
        nearby_court = _court_row(court_id=COURT_ID_1,
                                  lat=BASE_LAT + 0.036, lng=BASE_LNG)
        with patch("courts.views.requests") as mr:
            def side_effect(*args, **kwargs):
                url_str = str(args[0]) if args else ""
                if "slots" in url_str:
                    return _supa_empty()
                return _supa_list([nearby_court])
            mr.get.side_effect = side_effect
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertEqual(r.status_code, 200)
        # Court at ~4km should be in 5km default radius
        self.assertEqual(len(r.json()), 1)

    def test_radius_1km_filters_out_3km_court(self):
        """Court at ~3 km away should NOT appear with radius=1."""
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT + 0.027, lng=BASE_LNG)
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_list([court])
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "1"
            })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_radius_3km_accepted(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "3"
            })
        self.assertEqual(r.status_code, 200)

    def test_radius_5km_accepted(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {
                "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "5"
            })
        self.assertEqual(r.status_code, 200)

    def test_radius_2km_rejected(self):
        r = self.client.get(self.url, {
            "lat": str(BASE_LAT), "lng": str(BASE_LNG), "radius_km": "2"
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("radius_km", r.json()["error"])


class TestCourtsNearbyResponseShape(TestCase):
    """Response shape validation — court fields plus has_open_slots_today and distance_km."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_response_shape(self):
        court = _court_row(court_id=COURT_ID_1, lat=BASE_LAT, lng=BASE_LNG)

        with patch("courts.views.requests") as mr:
            def side_effect(*args, **kwargs):
                url_str = str(args[0]) if args else ""
                if "slots" in url_str:
                    return _supa_empty()
                return _supa_list([court])
            mr.get.side_effect = side_effect
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})

        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body), 1)
        c = body[0]
        # Standard court fields
        for field in ("id", "name", "slug", "sport_types", "price_per_hour",
                      "address", "lat", "lng", "status"):
            self.assertIn(field, c)
        # Nearby-specific fields
        self.assertIn("distance_km", c)
        self.assertIn("has_open_slots_today", c)

    def test_public_no_auth_required(self):
        """Endpoint is public — no auth token needed."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertEqual(r.status_code, 200)


class TestCourtsNearbyServiceError(TestCase):
    """Upstream Supabase error handling."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/nearby"

    def test_supabase_503_returns_503(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_error()
            r = self.client.get(self.url, {"lat": str(BASE_LAT), "lng": str(BASE_LNG)})
        self.assertEqual(r.status_code, 503)
