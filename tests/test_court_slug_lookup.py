"""
Tests for Court slug lookup endpoint (grava-3106.6).

Covers:
  grava-3106.6.1 -- GET /api/courts/by-slug/{slug} — public; returns same payload
                    as GET /courts/{id}; 404 if not found or status != approved
  grava-3106.6.2 -- Slug is case-insensitive; matched against lower(courts.slug)
  grava-3106.6.3 -- Used by customer app deep-link router for QR code scans (screen 07)

All Supabase HTTP calls are mocked.
"""
import uuid
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase

COURT_ID = str(uuid.uuid4())
OWNER_ID = str(uuid.uuid4())


def _court_row(slug="test-court", status="approved", court_id=None, **overrides):
    court_id = court_id or COURT_ID
    row = {
        "id": court_id,
        "owner_id": OWNER_ID,
        "name": "Test Court",
        "slug": slug,
        "sport_types": ["football"],
        "capacity": 22,
        "price_per_hour": "50.00",
        "operating_hours": {"mon": {"open": "06:00", "close": "22:00"}},
        "address": "123 Main St",
        "lat": "10.123",
        "lng": "106.456",
        "status": status,
        "amenities": ["parking"],
        "description": "A test court",
        "photos": [],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row


def _supa_single(**kw):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = [_court_row(**kw)]
    return r


def _supa_empty():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = []
    return r


def _supa_error():
    r = MagicMock()
    r.status_code = 500
    r.json.return_value = {"message": "Internal server error"}
    return r


class TestCourtSlugLookup(TestCase):
    """GET /api/courts/by-slug/{slug} -- public endpoint."""

    def setUp(self):
        self.client = Client()

    # ------------------------------------------------------------------ #
    # Happy path                                                           #
    # ------------------------------------------------------------------ #

    def test_returns_200_with_approved_court(self):
        """grava-3106.6.1 — Returns 200 for an approved court."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(slug="test-court", status="approved")
            r = self.client.get("/api/courts/by-slug/test-court")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["slug"], "test-court")
        self.assertEqual(body["status"], "approved")

    def test_response_shape_matches_court_detail(self):
        """grava-3106.6.1 — Response payload matches GET /courts/{id} shape."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(slug="test-court", status="approved")
            r = self.client.get("/api/courts/by-slug/test-court")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        for field in [
            "id", "owner_id", "name", "slug", "sport_types", "capacity",
            "price_per_hour", "operating_hours", "address", "lat", "lng",
            "status", "amenities", "description", "photos",
            "created_at", "updated_at",
        ]:
            self.assertIn(field, body, f"Missing field: {field}")

    def test_no_auth_required(self):
        """grava-3106.6.1 — Endpoint is public; no Authorization header needed."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(slug="public-court", status="approved")
            r = self.client.get("/api/courts/by-slug/public-court")
        self.assertEqual(r.status_code, 200)

    # ------------------------------------------------------------------ #
    # 404 cases                                                            #
    # ------------------------------------------------------------------ #

    def test_404_when_slug_not_found(self):
        """grava-3106.6.1 — 404 if no court has that slug."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_empty()
            r = self.client.get("/api/courts/by-slug/nonexistent-slug")
        self.assertEqual(r.status_code, 404)
        self.assertIn("error", r.json())

    def test_404_when_status_not_approved(self):
        """grava-3106.6.1 — 404 if court exists but status != approved."""
        for bad_status in ("pending", "suspended", "rejected"):
            with self.subTest(status=bad_status):
                with patch("courts.views.requests") as mr:
                    mr.get.return_value = _supa_single(slug="test-court", status=bad_status)
                    r = self.client.get("/api/courts/by-slug/test-court")
                self.assertEqual(r.status_code, 404)
                self.assertIn("error", r.json())

    # ------------------------------------------------------------------ #
    # Case-insensitive matching (grava-3106.6.2)                          #
    # ------------------------------------------------------------------ #

    def test_slug_lookup_is_case_insensitive(self):
        """grava-3106.6.2 — Slug query is lowercased before lookup."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(slug="test-court", status="approved")
            r = self.client.get("/api/courts/by-slug/TEST-COURT")
        self.assertEqual(r.status_code, 200)
        # Verify the slug was lowercased in the Supabase query
        call_args = mr.get.call_args
        params = call_args[1]["params"] if call_args[1] else call_args[0][1]
        # The slug param sent to Supabase must be the lowercased version
        slug_param = params.get("slug", "")
        self.assertIn("test-court", slug_param)

    def test_slug_mixed_case_resolved(self):
        """grava-3106.6.2 — Mixed-case slug in URL is lowercased for DB query."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_single(slug="my-court", status="approved")
            r = self.client.get("/api/courts/by-slug/My-Court")
        self.assertEqual(r.status_code, 200)

    # ------------------------------------------------------------------ #
    # Service errors                                                       #
    # ------------------------------------------------------------------ #

    def test_503_on_supabase_error(self):
        """Returns 503 when Supabase is unavailable."""
        with patch("courts.views.requests") as mr:
            mr.get.return_value = _supa_error()
            r = self.client.get("/api/courts/by-slug/any-slug")
        self.assertEqual(r.status_code, 503)
        self.assertIn("error", r.json())

    def test_503_on_request_exception(self):
        """Returns 503 when network request fails."""
        import requests as real_requests
        with patch("courts.views.requests") as mr:
            mr.get.side_effect = real_requests.exceptions.ConnectionError("down")
            mr.exceptions.ConnectionError = real_requests.exceptions.ConnectionError
            r = self.client.get("/api/courts/by-slug/any-slug")
        self.assertEqual(r.status_code, 503)

    # ------------------------------------------------------------------ #
    # Method not allowed                                                   #
    # ------------------------------------------------------------------ #

    def test_post_not_allowed(self):
        """Only GET is allowed on this endpoint."""
        r = self.client.post("/api/courts/by-slug/test-court", data={},
                             content_type="application/json")
        self.assertEqual(r.status_code, 405)

    def test_patch_not_allowed(self):
        """PATCH is not allowed on this endpoint."""
        r = self.client.patch("/api/courts/by-slug/test-court", data={},
                              content_type="application/json")
        self.assertEqual(r.status_code, 405)
