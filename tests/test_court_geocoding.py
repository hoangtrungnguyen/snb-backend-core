"""
Tests for court geocoding on create/update (grava-5044.2).

Covers:
  grava-5044.2.1 -- POST /courts and PATCH /courts/{id} call Google Maps Geocoding API
  grava-5044.2.2 -- Stores lat, lng on court record
  grava-5044.2.3 -- If geocoding fails: court saved with lat=null, lng=null
  grava-5044.2.4 -- Geocoding formatted_address stored as canonical address

All Supabase HTTP calls and Google Maps API calls are mocked.
No real API calls are made.
"""
import json
import uuid
from unittest.mock import MagicMock, patch, call

from django.test import Client, TestCase

OWNER_ID = str(uuid.uuid4())
COURT_ID = str(uuid.uuid4())


def _owner_payload(uid=None):
    uid = uid or OWNER_ID
    return {"sub": uid, "email": "owner@example.com", "app_metadata": {"role": "owner"}}


def _court_row(court_id=None, owner_id=None, **overrides):
    court_id = court_id or COURT_ID
    owner_id = owner_id or OWNER_ID
    row = {
        "id": court_id,
        "owner_id": owner_id,
        "name": "Test Court",
        "slug": "test-court",
        "sport_types": ["football"],
        "capacity": 22,
        "price_per_hour": "50.00",
        "operating_hours": {"mon": {"open": "06:00", "close": "22:00"}},
        "address": "1 Nguyen Hue, Ho Chi Minh City, Vietnam",
        "lat": "10.7769",
        "lng": "106.7009",
        "status": "pending",
        "amenities": ["parking"],
        "description": "A test court",
        "photos": [],
        "auto_approve_single": False,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _geo_ok_resp(lat=10.7769, lng=106.7009, formatted="1 Nguyen Hue, Ho Chi Minh City, Vietnam"):
    """Successful Google Maps Geocoding API response."""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        "results": [
            {
                "formatted_address": formatted,
                "geometry": {"location": {"lat": lat, "lng": lng}},
            }
        ],
        "status": "OK",
    }
    return r


def _geo_empty_resp():
    """Google Maps Geocoding API response with no results."""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"results": [], "status": "ZERO_RESULTS"}
    return r


def _supa_empty():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = []
    return r


def _supa_insert(lat=None, lng=None, address=None):
    r = MagicMock()
    r.status_code = 201
    row = _court_row()
    if lat is not None:
        row["lat"] = str(lat)
    if lng is not None:
        row["lng"] = str(lng)
    if address is not None:
        row["address"] = address
    r.json.return_value = [row]
    return r


def _supa_single(**overrides):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = [_court_row(**overrides)]
    return r


def _supa_patch(**overrides):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = [_court_row(**overrides)]
    return r


# ---------------------------------------------------------------------------
# Tests for _geocode_address helper
# ---------------------------------------------------------------------------

class TestGeocodeAddressHelper(TestCase):
    """Unit tests for the _geocode_address helper function."""

    def test_returns_lat_lng_formatted_on_success(self):
        """_geocode_address returns (lat, lng, formatted_address) on success."""
        from courts.views import _geocode_address
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _geo_ok_resp(
                lat=10.7769, lng=106.7009,
                formatted="1 Nguyen Hue, District 1, Ho Chi Minh City, Vietnam"
            )
            result = _geocode_address("1 Nguyen Hue, HCMC")
        # Result should be a 3-tuple or dict with lat, lng, formatted_address
        lat, lng, formatted = result
        self.assertAlmostEqual(lat, 10.7769)
        self.assertAlmostEqual(lng, 106.7009)
        self.assertEqual(formatted, "1 Nguyen Hue, District 1, Ho Chi Minh City, Vietnam")

    def test_returns_none_on_empty_results(self):
        """_geocode_address returns (None, None, None) when geocoder returns no results."""
        from courts.views import _geocode_address
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _geo_empty_resp()
            result = _geocode_address("nonexistent address xyz")
        lat, lng, formatted = result
        self.assertIsNone(lat)
        self.assertIsNone(lng)
        self.assertIsNone(formatted)

    def test_returns_none_on_network_error(self):
        """_geocode_address returns (None, None, None) on network exception."""
        import requests as _requests
        from courts.views import _geocode_address
        with patch("courts.views.requests") as mr:
            mr.get.side_effect = _requests.RequestException("timeout")
            result = _geocode_address("some address")
        lat, lng, formatted = result
        self.assertIsNone(lat)
        self.assertIsNone(lng)
        self.assertIsNone(formatted)

    def test_calls_google_maps_api(self):
        """_geocode_address calls the Google Maps Geocoding API endpoint."""
        from courts.views import _geocode_address
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _geo_ok_resp()
            _geocode_address("123 Main St")
        mr.get.assert_called_once()
        call_args = mr.get.call_args
        url = call_args[0][0]
        self.assertIn("maps.googleapis.com", url)
        self.assertIn("geocode", url)


# ---------------------------------------------------------------------------
# Tests for POST /courts — geocoding on create (grava-5044.2.1, 5044.2.2, 5044.2.4)
# ---------------------------------------------------------------------------

class TestPostCourtsGeocoding(TestCase):
    """POST /courts geocoding integration tests."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/courts/"

    def _post(self, payload, geo_resp=None, supabase_insert=None):
        """Helper: POST /courts with mocked auth, geocoding, and Supabase."""
        geo_resp = geo_resp or _geo_ok_resp()
        supabase_insert = supabase_insert or _supa_insert()
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            # Side effects: first GET = slug-uniqueness check, second GET = geocoding
            mr.get.side_effect = [_supa_empty(), geo_resp]
            mr.post.return_value = supabase_insert
            return self.client.post(
                self.url,
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )

    def test_geocoding_called_on_create_with_address(self):
        """grava-5044.2.1: POST with address triggers geocoding API call."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_empty(), _geo_ok_resp()]
            mr.post.return_value = _supa_insert()
            r = self.client.post(
                self.url,
                data=json.dumps({"name": "Test Court", "address": "1 Nguyen Hue, HCMC"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 201)
        # Verify Google Maps was called (second get call is geocoding)
        calls = mr.get.call_args_list
        geo_call = calls[1]
        self.assertIn("maps.googleapis.com", geo_call[0][0])

    def test_lat_lng_stored_from_geocoding(self):
        """grava-5044.2.2: lat and lng from geocoding response are stored on court."""
        expected_lat = 10.7769
        expected_lng = 106.7009
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_empty(), _geo_ok_resp(lat=expected_lat, lng=expected_lng)]
            mr.post.return_value = _supa_insert(lat=expected_lat, lng=expected_lng)
            r = self.client.post(
                self.url,
                data=json.dumps({"name": "Geo Court", "address": "123 Main St"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 201)
        # Verify the insert payload includes lat/lng
        insert_payload = mr.post.call_args[1]["json"]
        self.assertAlmostEqual(insert_payload["lat"], expected_lat)
        self.assertAlmostEqual(insert_payload["lng"], expected_lng)

    def test_formatted_address_stored_on_create(self):
        """grava-5044.2.4: formatted_address from geocoding overwrites address on insert."""
        formatted = "1 Nguyen Hue, District 1, Ho Chi Minh City 700000, Vietnam"
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [
                _supa_empty(),
                _geo_ok_resp(formatted=formatted),
            ]
            mr.post.return_value = _supa_insert(address=formatted)
            r = self.client.post(
                self.url,
                data=json.dumps({"name": "Formatted Court", "address": "1 Nguyen Hue, HCMC"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 201)
        # The insert should use formatted_address, not the raw input
        insert_payload = mr.post.call_args[1]["json"]
        self.assertEqual(insert_payload["address"], formatted)

    def test_geocoding_failure_on_create_saves_null_lat_lng(self):
        """grava-5044.2.3: POST with geocoding failure → lat=null, lng=null, 201."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_empty(), _geo_empty_resp()]
            mr.post.return_value = _supa_insert(lat=None, lng=None)
            r = self.client.post(
                self.url,
                data=json.dumps({"name": "No Geo Court", "address": "unknown place xyz"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 201)
        # Insert should have lat=None, lng=None
        insert_payload = mr.post.call_args[1]["json"]
        self.assertIsNone(insert_payload.get("lat"))
        self.assertIsNone(insert_payload.get("lng"))

    def test_geocoding_failure_on_create_preserves_original_address(self):
        """grava-5044.2.3: When geocoding fails, original address is stored."""
        raw_address = "some unknown place xyz"
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_empty(), _geo_empty_resp()]
            mr.post.return_value = _supa_insert(address=raw_address)
            r = self.client.post(
                self.url,
                data=json.dumps({"name": "No Geo Court", "address": raw_address}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 201)
        insert_payload = mr.post.call_args[1]["json"]
        self.assertEqual(insert_payload["address"], raw_address)

    def test_no_address_skips_geocoding(self):
        """POST without address field does not call geocoding API."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            mr.post.return_value = _supa_insert()
            r = self.client.post(
                self.url,
                data=json.dumps({"name": "No Address Court"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 201)
        # Only one GET call (slug uniqueness), not two (not geocoding)
        self.assertEqual(mr.get.call_count, 1)


# ---------------------------------------------------------------------------
# Tests for PATCH /courts/{id} — geocoding on update (grava-5044.2.1, 5044.2.2, 5044.2.4)
# ---------------------------------------------------------------------------

class TestPatchCourtGeocoding(TestCase):
    """PATCH /courts/{id} geocoding integration tests."""

    def setUp(self):
        self.client = Client()

    def _patch(self, payload, geo_resp=None, supa_patch_resp=None, court_owner=None):
        """Helper: PATCH /courts/{id} with mocked auth, geocoding, and Supabase."""
        geo_resp = geo_resp or _geo_ok_resp()
        supa_patch_resp = supa_patch_resp or _supa_patch()
        court_owner = court_owner or OWNER_ID
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            # First GET = fetch court for ownership check; second GET = geocoding
            mr.get.side_effect = [_supa_single(owner_id=court_owner), geo_resp]
            mr.patch.return_value = supa_patch_resp
            return self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )

    def test_geocoding_called_on_patch_with_address(self):
        """grava-5044.2.1: PATCH with address change triggers geocoding API call."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_single(), _geo_ok_resp()]
            mr.patch.return_value = _supa_patch()
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"address": "New Address, Ho Chi Minh City"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 200)
        calls = mr.get.call_args_list
        # Second call should be geocoding
        self.assertEqual(mr.get.call_count, 2)
        geo_call = calls[1]
        self.assertIn("maps.googleapis.com", geo_call[0][0])

    def test_patch_without_address_skips_geocoding(self):
        """PATCH without address does not call geocoding API."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single()
            mr.patch.return_value = _supa_patch()
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"name": "Updated Name"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 200)
        # Only one GET (ownership check), no geocoding call
        self.assertEqual(mr.get.call_count, 1)

    def test_patch_lat_lng_stored_from_geocoding(self):
        """grava-5044.2.2: lat/lng from geocoding are stored during PATCH."""
        expected_lat = 21.0278
        expected_lng = 105.8342
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [
                _supa_single(),
                _geo_ok_resp(lat=expected_lat, lng=expected_lng),
            ]
            mr.patch.return_value = _supa_patch(lat=str(expected_lat), lng=str(expected_lng))
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"address": "1 Ba Dinh, Hanoi"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 200)
        patch_payload = mr.patch.call_args[1]["json"]
        self.assertAlmostEqual(patch_payload["lat"], expected_lat)
        self.assertAlmostEqual(patch_payload["lng"], expected_lng)

    def test_patch_formatted_address_stored(self):
        """grava-5044.2.4: formatted_address from geocoding overwrites address on PATCH."""
        formatted = "1 Ba Dinh Square, Ba Dinh, Hanoi, Vietnam"
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_single(), _geo_ok_resp(formatted=formatted)]
            mr.patch.return_value = _supa_patch(address=formatted)
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"address": "1 Ba Dinh, Hanoi"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertEqual(r.status_code, 200)
        patch_payload = mr.patch.call_args[1]["json"]
        self.assertEqual(patch_payload["address"], formatted)

    def test_patch_geocoding_failure_saves_null_lat_lng(self):
        """grava-5044.2.3: PATCH with geocoding failure → lat=null, lng=null, court saved."""
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_single(), _geo_empty_resp()]
            mr.patch.return_value = _supa_patch(lat=None, lng=None)
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"address": "unknown xyz place"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        # Court should be saved even with geocoding failure
        self.assertIn(r.status_code, (200, 207))
        patch_payload = mr.patch.call_args[1]["json"]
        self.assertIsNone(patch_payload.get("lat"))
        self.assertIsNone(patch_payload.get("lng"))

    def test_patch_geocoding_failure_preserves_provided_address(self):
        """grava-5044.2.3: When geocoding fails on PATCH, address as-provided is stored."""
        raw_address = "unknown xyz place"
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [_supa_single(), _geo_empty_resp()]
            mr.patch.return_value = _supa_patch(address=raw_address)
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"address": raw_address}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertIn(r.status_code, (200, 207))
        patch_payload = mr.patch.call_args[1]["json"]
        self.assertEqual(patch_payload["address"], raw_address)

    def test_patch_geocoding_network_error_saves_null_lat_lng(self):
        """grava-5044.2.3: Network error during geocoding on PATCH → null lat/lng."""
        import requests as _requests
        with patch("auth_ext.middleware._decode_token", return_value=_owner_payload()), \
             patch("courts.views.requests") as mr:
            mr.get.side_effect = [
                _supa_single(),
                _requests.RequestException("Connection refused"),
            ]
            mr.patch.return_value = _supa_patch(lat=None, lng=None)
            r = self.client.patch(
                f"/api/courts/{COURT_ID}/",
                data=json.dumps({"address": "123 Main St"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer valid.token",
            )
        self.assertIn(r.status_code, (200, 207))
        patch_payload = mr.patch.call_args[1]["json"]
        self.assertIsNone(patch_payload.get("lat"))
        self.assertIsNone(patch_payload.get("lng"))
