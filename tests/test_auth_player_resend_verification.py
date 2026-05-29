"""
Tests for POST /auth/player/resend-verification endpoint.

Covers:
- Successful send → 200 {"message": "Verification email sent"}
- Rate limit enforced → 429 {"error": "rate_limited", "retry_after": <seconds>}
- retry_after value is correct (close to 60)
- Supabase error still returns 200 (anti-enumeration)
- Rate limit resets after 60s (mock cache TTL expiry)
- Missing email → 400
- Non-JSON body → 400
"""
import json
import time
from unittest.mock import patch, MagicMock

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")
os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key")

from django.test import TestCase, Client, override_settings
from django.core.cache import cache


RATE_LIMIT_CACHE_KEY = "resend_verification:{email}"
RATE_LIMIT_SECONDS = 60


@override_settings(
    SUPABASE_URL="https://test.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="test-anon-key",
    APP_BASE_URL="https://myapp.example.com",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    },
)
class PlayerResendVerificationViewTests(TestCase):
    """Tests for POST /auth/player/resend-verification."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/resend-verification"
        # Clear cache between tests to avoid cross-test contamination
        cache.clear()

    def _mock_supabase_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        return mock_resp

    def _mock_supabase_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.json.return_value = {"error": "User not found"}
        return mock_resp

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_successful_send_returns_200(self):
        """Valid email, no rate limit → 200 with success message."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["message"], "Verification email sent")

    def test_calls_supabase_resend_endpoint(self):
        """Endpoint must call Supabase /auth/v1/resend with correct params."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com"}),
                content_type="application/json",
            )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        called_url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        self.assertIn("/auth/v1/resend", called_url)
        posted_json = call_kwargs[1].get("json") or {}
        self.assertEqual(posted_json.get("type"), "signup")
        self.assertEqual(posted_json.get("email"), "player@example.com")
        self.assertIn("/auth/callback?type=email", posted_json.get("redirect_to", ""))

    # ------------------------------------------------------------------
    # Anti-enumeration: Supabase error still 200
    # ------------------------------------------------------------------

    def test_supabase_error_still_returns_200(self):
        """Even if Supabase returns an error, endpoint returns 200 (anti-enumeration)."""
        mock_resp = self._mock_supabase_error()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "nonexistent@example.com"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["message"], "Verification email sent")

    def test_supabase_network_error_still_returns_200(self):
        """Even if Supabase is unreachable, endpoint returns 200 (anti-enumeration)."""
        import requests as req_lib

        with patch("auth_ext.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "player@example.com"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["message"], "Verification email sent")

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def test_rate_limit_enforced_on_second_request(self):
        """Second request within 60s for same email → 429."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            # First request — should succeed
            resp1 = self.client.post(
                self.url,
                data=json.dumps({"email": "ratelimited@example.com"}),
                content_type="application/json",
            )
            # Second request immediately — should be rate limited
            resp2 = self.client.post(
                self.url,
                data=json.dumps({"email": "ratelimited@example.com"}),
                content_type="application/json",
            )

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 429)

    def test_rate_limit_returns_correct_error_body(self):
        """429 response must include 'error': 'rate_limited' and 'retry_after'."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            self.client.post(
                self.url,
                data=json.dumps({"email": "ratelimited@example.com"}),
                content_type="application/json",
            )
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "ratelimited@example.com"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 429)
        body = resp.json()
        self.assertEqual(body["error"], "rate_limited")
        self.assertIn("retry_after", body)
        self.assertIsInstance(body["retry_after"], int)

    def test_retry_after_value_is_near_60(self):
        """retry_after must be close to 60 seconds (between 55 and 60 inclusive)."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            self.client.post(
                self.url,
                data=json.dumps({"email": "ratelimited@example.com"}),
                content_type="application/json",
            )
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "ratelimited@example.com"}),
                content_type="application/json",
            )

        body = resp.json()
        retry_after = body["retry_after"]
        # Should be close to 60 — allow a small window for test execution time
        self.assertGreaterEqual(retry_after, 55)
        self.assertLessEqual(retry_after, 60)

    def test_rate_limit_is_per_email(self):
        """Rate limit is per email address — different emails are independent."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            # Exhaust rate limit for email A
            self.client.post(
                self.url,
                data=json.dumps({"email": "emailA@example.com"}),
                content_type="application/json",
            )
            # Email B should not be rate limited
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "emailB@example.com"}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 200)

    def test_rate_limit_resets_after_60s(self):
        """After the cache key expires (mocked), subsequent request succeeds."""
        mock_resp = self._mock_supabase_success()
        email = "resettest@example.com"
        cache_key = f"resend_verification:{email}"

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            # First request sets the rate limit
            resp1 = self.client.post(
                self.url,
                data=json.dumps({"email": email}),
                content_type="application/json",
            )
            self.assertEqual(resp1.status_code, 200)

            # Simulate cache expiry by deleting the key
            cache.delete(cache_key)

            # Now request should succeed again
            resp2 = self.client.post(
                self.url,
                data=json.dumps({"email": email}),
                content_type="application/json",
            )

        self.assertEqual(resp2.status_code, 200)
        body = resp2.json()
        self.assertEqual(body["message"], "Verification email sent")

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_missing_email_returns_400(self):
        """Request without email field → 400 without hitting Supabase."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_non_json_body_returns_400(self):
        """Non-JSON body → 400."""
        resp = self.client.post(
            self.url,
            data="not-json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_wrong_http_method_returns_405(self):
        """GET request → 405 Method Not Allowed."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_case_variant_email_hits_rate_limit(self):
        """Case variants of the same email share a rate limit bucket."""
        mock_resp = self._mock_supabase_success()

        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            # First request with lowercase email
            resp1 = self.client.post(
                self.url,
                data=json.dumps({"email": "user@example.com"}),
                content_type="application/json",
            )
            # Second request with uppercase variant — must hit the same bucket
            resp2 = self.client.post(
                self.url,
                data=json.dumps({"email": "USER@EXAMPLE.COM"}),
                content_type="application/json",
            )

        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 429)

    def test_whitespace_only_email_returns_400(self):
        """Whitespace-only email string is treated as missing → 400."""
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(
                self.url,
                data=json.dumps({"email": "   "}),
                content_type="application/json",
            )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(body["error"], "validation_error")
        self.assertIn("email", body.get("detail", ""))
        mock_post.assert_not_called()
