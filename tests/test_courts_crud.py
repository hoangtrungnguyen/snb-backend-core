"""
Tests for Court CRUD endpoints (grava-3106.1).

Covers:
  grava-3106.1.1 -- POST /courts
  grava-3106.1.2 -- operating_hours schema validation
  grava-3106.1.3 -- Google Maps Geocoding on create
  grava-3106.1.4 -- Auto-generated slug
  grava-3106.1.5 -- GET /courts/{id} (public)
  grava-3106.1.6 -- PATCH /courts/{id} (owner only, partial update)
  grava-3106.1.7 -- DELETE /courts/{id} (sets status=suspended; 409 on active bookings)
  grava-3106.1.8 -- GET /courts (paginated, filters)

All Supabase HTTP calls and JWT auth are mocked.
"""
import json
import uuid
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

OWNER_ID = str(uuid.uuid4())
OTHER_OWNER_ID = str(uuid.uuid4())
COURT_ID = str(uuid.uuid4())


def _owner_payload(uid=None):
    uid = uid or OWNER_ID
    return {"sub": uid, "email": "owner@example.com", "app_metadata": {"role": "owner"}}


def _player_payload(uid=None):
    uid = uid or str(uuid.uuid4())
    return {"sub": uid, "email": "player@example.com", "app_metadata": {"role": "player"}}


def _court_row(court_id=None, owner_id=None, **overrides):
    court_id = court_id or COURT_ID
    owner_id = owner_id or OWNER_ID
    row = {
        "id": court_id, "owner_id": owner_id, "name": "Test Court",
        "slug": "test-court", "sport_types": ["football"], "capacity": 22,
        "price_per_hour": "50.00",
        "operating_hours": {"mon": {"open": "06:00", "close": "22:00"}},
        "address": "123 Main St", "lat": "10.123", "lng": "106.456",
        "status": "pending", "amenities": ["parking"],
        "description": "A test court", "photos": [],
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _geo_resp(lat=10.123, lng=106.456):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "results": [{"geometry": {"location": {"lat": lat, "lng": lng}}}],
        "status": "OK",
    }
    return r


def _supa_insert(court_id=None, owner_id=None):
    r = MagicMock()
    r.status_code = 201
    r.json.return_value = [_court_row(court_id=court_id, owner_id=owner_id)]
    return r


def _supa_single(court_id=None, owner_id=None, **kw):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = [_court_row(court_id=court_id, owner_id=owner_id, **kw)]
    return r


def _supa_empty():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = []
    return r


def _supa_list(rows):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = rows
    return r


def _supa_patch(court_id=None, owner_id=None, **kw):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = [_court_row(court_id=court_id, owner_id=owner_id, **kw)]
    return r


class TestPostCourts(TestCase):
    """POST /api/courts/ -- owner only."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/"

    def _post(self, payload, tp=None):
        tp = tp or _owner_payload()
        with patch("auth_ext.middleware._decode_token", return_value=tp), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_empty(), _geo_resp()]
            mr.post.return_value = _supa_insert()
            return self.client.post(
                self.url, data=json.dumps(payload), content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )

    def test_create_returns_201(self):
        r = self._post({"name": "Test Court", "sport_types": ["football"],
                        "operating_hours": {"mon": {"open": "06:00", "close": "22:00"}},
                        "address": "123 Main St"})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertIn("id", body)
        self.assertIn("slug", body)

    def test_create_requires_name(self):
        r = self._post({"sport_types": ["football"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_create_requires_owner_role(self):
        r = self._post({"name": "Court"}, tp=_player_payload())
        self.assertEqual(r.status_code, 403)

    def test_create_unauthenticated_401(self):
        r = self.client.post(self.url, data=json.dumps({"name": "Court"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_create_includes_lat_lng(self):
        r = self._post({"name": "Geo Court", "sport_types": ["tennis"], "address": "456 Elm"})
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertIn("lat", body)
        self.assertIn("lng", body)

    def test_create_generates_slug(self):
        r = self._post({"name": "My Amazing Court", "sport_types": ["badminton"]})
        self.assertEqual(r.status_code, 201)
        self.assertIn("slug", r.json())

    def _post_bad_hours(self, hours):
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()):
            return self.client.post(
                self.url,
                data=json.dumps({"name": "Court", "sport_types": ["x"],
                                 "operating_hours": hours}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )

    def test_bad_time_400(self):
        r = self._post_bad_hours({"mon": {"open": "25:00", "close": "22:00"}})
        self.assertEqual(r.status_code, 400)

    def test_missing_close_400(self):
        r = self._post_bad_hours({"mon": {"open": "08:00"}})
        self.assertEqual(r.status_code, 400)

    def test_bad_day_400(self):
        r = self._post_bad_hours({"funday": {"open": "08:00", "close": "22:00"}})
        self.assertEqual(r.status_code, 400)


class TestGetCourtById(TestCase):
    """GET /api/courts/{id}/ -- public."""

    def setUp(self):
        self.client = Client()

    def test_get_200(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single()
            r = self.client.get(f"/api/courts/{COURT_ID}/")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for k in ("id", "name", "slug", "sport_types", "operating_hours", "photos"):
            self.assertIn(k, body)

    def test_get_404(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get(f"/api/courts/{uuid.uuid4()}/")
        self.assertEqual(r.status_code, 404)

    def test_get_public_no_auth(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single()
            r = self.client.get(f"/api/courts/{COURT_ID}/")
        self.assertEqual(r.status_code, 200)

    def test_get_503_on_network_error(self):
        import requests as rl
        with patch("courts.views.requests") as mr:
            mr.get.side_effect = rl.RequestException("error")
            r = self.client.get(f"/api/courts/{COURT_ID}/")
        self.assertEqual(r.status_code, 503)


class TestPatchCourt(TestCase):
    """PATCH /api/courts/{id}/ -- owner only."""

    def setUp(self):
        self.client = Client()

    def _patch(self, court_id, payload, token_uid=None, court_owner=None):
        token_uid = token_uid or OWNER_ID
        court_owner = court_owner or OWNER_ID
        with patch("auth_ext.middleware._decode_token",
                   return_value=_owner_payload(uid=token_uid)), \
             patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(court_id=court_id, owner_id=court_owner)
            mr.patch.return_value = _supa_patch(court_id=court_id, owner_id=court_owner)
            return self.client.patch(
                f"/api/courts/{court_id}/", data=json.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )

    def test_patch_200(self):
        r = self._patch(COURT_ID, {"name": "Updated"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("id", r.json())

    def test_patch_requires_auth(self):
        r = self.client.patch(f"/api/courts/{COURT_ID}/",
                              data=json.dumps({"name": "X"}),
                              content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_patch_player_403(self):
        with patch("auth_ext.middleware._decode_token", return_value=_player_payload()):
            r = self.client.patch(f"/api/courts/{COURT_ID}/",
                                  data=json.dumps({"name": "X"}),
                                  content_type="application/json",
                                  HTTP_AUTHORIZATION="Bearer valid.token")
        self.assertEqual(r.status_code, 403)

    def test_patch_other_owner_403(self):
        r = self._patch(COURT_ID, {"name": "Hijack"},
                        token_uid=OTHER_OWNER_ID, court_owner=OWNER_ID)
        self.assertEqual(r.status_code, 403)

    def test_patch_not_found_404(self):
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.patch(f"/api/courts/{uuid.uuid4()}/",
                                  data=json.dumps({"name": "X"}),
                                  content_type="application/json",
                                  HTTP_AUTHORIZATION="Bearer valid.token")
        self.assertEqual(r.status_code, 404)

    def test_patch_invalid_hours_400(self):
        with patch("auth_ext.middleware._decode_token",
                   return_value=_owner_payload(uid=OWNER_ID)), \
             patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(court_id=COURT_ID, owner_id=OWNER_ID)
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"operating_hours": {
                    "mon": {"open": "99:00", "close": "22:00"}}}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 400)


class TestDeleteCourt(TestCase):
    """DELETE /api/courts/{id}/ -- sets status=suspended."""

    def setUp(self):
        self.client = Client()

    def _delete(self, court_id, token_uid=None, court_owner=None, n_bookings=0):
        token_uid = token_uid or OWNER_ID
        court_owner = court_owner or OWNER_ID
        with patch("auth_ext.middleware._decode_token",
                   return_value=_owner_payload(uid=token_uid)), \
             patch("courts.views.requests") as mr:
            court_r = _supa_single(court_id=court_id, owner_id=court_owner)
            bookings_r = MagicMock()
            bookings_r.status_code = 200
            bookings_r.json.return_value = [
                {"id": str(uuid.uuid4())} for _ in range(n_bookings)
            ]
            suspend_r = _supa_patch(court_id=court_id, owner_id=court_owner,
                                    status="suspended")
            mr.get.side_effect = [court_r, bookings_r]
            mr.patch.return_value = suspend_r
            return self.client.delete(f"/api/courts/{court_id}/",
                                      HTTP_AUTHORIZATION="Bearer valid.token")

    def test_delete_200_suspended(self):
        r = self._delete(COURT_ID)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json().get("status"), "suspended")

    def test_delete_requires_auth(self):
        r = self.client.delete(f"/api/courts/{COURT_ID}/")
        self.assertEqual(r.status_code, 401)

    def test_delete_player_403(self):
        with patch("auth_ext.middleware._decode_token", return_value=_player_payload()):
            r = self.client.delete(f"/api/courts/{COURT_ID}/",
                                   HTTP_AUTHORIZATION="Bearer valid.token")
        self.assertEqual(r.status_code, 403)

    def test_delete_other_owner_403(self):
        with patch("auth_ext.middleware._decode_token",
                   return_value=_owner_payload(uid=OTHER_OWNER_ID)), \
             patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(court_id=COURT_ID, owner_id=OWNER_ID)
            r = self.client.delete(f"/api/courts/{COURT_ID}/",
                                   HTTP_AUTHORIZATION="Bearer valid.token")
        self.assertEqual(r.status_code, 403)

    def test_delete_not_found_404(self):
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.delete(f"/api/courts/{uuid.uuid4()}/",
                                   HTTP_AUTHORIZATION="Bearer valid.token")
        self.assertEqual(r.status_code, 404)

    def test_delete_active_bookings_409(self):
        r = self._delete(COURT_ID, n_bookings=2)
        self.assertEqual(r.status_code, 409)
        self.assertIn("error", r.json())

    def test_delete_row_still_returned(self):
        r = self._delete(COURT_ID)
        self.assertEqual(r.status_code, 200)
        self.assertIn("id", r.json())


class TestListCourts(TestCase):
    """GET /api/courts/ -- paginated, public, filterable."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/"

    def _get(self, params=None):
        with patch("courts.views.requests") as mr:
            rows = [_court_row(court_id=str(uuid.uuid4())) for _ in range(2)]
            mr.get.return_value = _supa_list(rows)
            return self.client.get(self.url, params or {})

    def test_list_200(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json(), (list, dict))

    def test_list_no_auth(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)

    def test_filter_owner_id(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_list([_court_row()])
            r = self.client.get(self.url, {"owner_id": OWNER_ID})
        self.assertEqual(r.status_code, 200)

    def test_filter_sport_type(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_list([_court_row()])
            r = self.client.get(self.url, {"sport_type": "football"})
        self.assertEqual(r.status_code, 200)

    def test_filter_status(self):
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_list([])
            r = self.client.get(self.url, {"status": "approved"})
        self.assertEqual(r.status_code, 200)

    def test_list_503_on_error(self):
        import requests as rl
        with patch("courts.views.requests") as mr:
            mr.get.side_effect = rl.RequestException("timeout")
            r = self.client.get(self.url)
        self.assertEqual(r.status_code, 503)


class TestSlugGeneration(TestCase):
    """Unit tests for _generate_slug (grava-3106.1.4)."""

    def test_basic(self):
        from courts.views import _generate_slug
        self.assertEqual(_generate_slug("Test Court"), "test-court")

    def test_lowercased(self):
        from courts.views import _generate_slug
        slug = _generate_slug("ABC DEF")
        self.assertEqual(slug, slug.lower())

    def test_strips_edges(self):
        from courts.views import _generate_slug
        slug = _generate_slug("  My   Court  ")
        self.assertFalse(slug.startswith("-"))
        self.assertFalse(slug.endswith("-"))

    def test_removes_special_chars(self):
        from courts.views import _generate_slug
        slug = _generate_slug("Court@2026!")
        self.assertNotIn("@", slug)
        self.assertNotIn("!", slug)


class TestOperatingHoursValidation(TestCase):
    """Unit tests for _validate_operating_hours (grava-3106.1.2)."""

    def _v(self, hours):
        from courts.views import _validate_operating_hours
        _validate_operating_hours(hours)

    def _vfail(self, hours):
        from courts.views import _validate_operating_hours
        with self.assertRaises(ValueError):
            _validate_operating_hours(hours)

    def test_valid(self):
        self._v({"mon": {"open": "06:00", "close": "22:00"}})

    def test_all_days(self):
        self._v({d: {"open": "06:00", "close": "22:00"}
                 for d in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]})

    def test_bad_day(self):
        self._vfail({"funday": {"open": "06:00", "close": "22:00"}})

    def test_bad_time(self):
        self._vfail({"mon": {"open": "25:00", "close": "22:00"}})

    def test_missing_open(self):
        self._vfail({"mon": {"close": "22:00"}})

    def test_missing_close(self):
        self._vfail({"mon": {"open": "06:00"}})

    def test_none_ok(self):
        self._v(None)

    def test_not_dict(self):
        self._vfail("not-a-dict")
