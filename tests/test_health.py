"""Tests for the health check endpoint GET /health/."""
import json

from django.test import TestCase


class HealthCheckTests(TestCase):
    """Test suite for GET /health/."""

    def test_health_returns_200_with_ok_payload(self):
        """Health endpoint returns HTTP 200 with all checks passing."""
        response = self.client.get("/health/")
        self.assertEqual(response.status_code, 200)

    def test_health_returns_json_content_type(self):
        """Health endpoint returns JSON content type."""
        response = self.client.get("/health/")
        self.assertEqual(response["Content-Type"], "application/json")

    def test_health_payload_has_required_keys(self):
        """Health payload contains status, db, and realtime keys."""
        response = self.client.get("/health/")
        data = json.loads(response.content)
        self.assertIn("status", data)
        self.assertIn("db", data)
        self.assertIn("realtime", data)

    def test_health_payload_status_ok(self):
        """Health payload status is 'ok' when all checks pass."""
        response = self.client.get("/health/")
        data = json.loads(response.content)
        self.assertEqual(data["status"], "ok")

    def test_health_payload_db_ok(self):
        """Health payload db is 'ok' when database is reachable."""
        response = self.client.get("/health/")
        data = json.loads(response.content)
        self.assertEqual(data["db"], "ok")

    def test_health_payload_realtime_ok(self):
        """Health payload realtime is 'ok' (stub)."""
        response = self.client.get("/health/")
        data = json.loads(response.content)
        self.assertEqual(data["realtime"], "ok")

    def test_health_no_authentication_required(self):
        """Health endpoint does not require authentication."""
        # Client is anonymous (no login) — should still return 200
        response = self.client.get("/health/")
        # Must not be a redirect to login or a 401/403
        self.assertNotIn(response.status_code, [301, 302, 401, 403])
        self.assertEqual(response.status_code, 200)
