"""
Tests for GET /auth/player/google endpoint.

Validates that the view returns HTTP 302 redirecting to the Supabase
Google OAuth URL, handles optional redirect_to query param, and returns
503 when SUPABASE_URL is not configured.

No real network requests are made.
"""
import os
from urllib.parse import urlparse, parse_qs, unquote_plus

import django
from django.test import TestCase, Client, override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")


class PlayerGoogleOAuthViewTests(TestCase):
    """Tests for GET /auth/player/google endpoint."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/google"

    # ------------------------------------------------------------------
    # Success path — 302 redirect
    # ------------------------------------------------------------------

    @override_settings(SUPABASE_URL="https://xyzproject.supabase.co")
    def test_redirect_returns_302(self):
        """GET /auth/player/google → 302 redirect."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)

    @override_settings(SUPABASE_URL="https://xyzproject.supabase.co")
    def test_redirect_location_contains_supabase_authorize(self):
        """Redirect Location header must point to Supabase /auth/v1/authorize."""
        resp = self.client.get(self.url)
        location = resp["Location"]
        self.assertIn("https://xyzproject.supabase.co/auth/v1/authorize", location)

    @override_settings(SUPABASE_URL="https://xyzproject.supabase.co")
    def test_redirect_location_contains_provider_google(self):
        """Redirect Location must include provider=google query param."""
        resp = self.client.get(self.url)
        location = resp["Location"]
        self.assertIn("provider=google", location)

    @override_settings(SUPABASE_URL="https://xyzproject.supabase.co")
    def test_redirect_location_contains_redirect_to(self):
        """Redirect Location must include redirect_to pointing to /auth/callback."""
        resp = self.client.get(self.url)
        location = resp["Location"]
        self.assertIn("redirect_to=", location)
        # The callback URL is URL-encoded in the query string; decode it for assertion.
        qs = parse_qs(urlparse(location).query)
        redirect_to_value = unquote_plus(qs.get("redirect_to", [""])[0])
        self.assertIn("/auth/callback", redirect_to_value)

    # ------------------------------------------------------------------
    # Client-supplied redirect_to is stripped (open-redirect prevention)
    # ------------------------------------------------------------------

    @override_settings(SUPABASE_URL="https://xyzproject.supabase.co")
    def test_client_redirect_to_is_not_passed_through(self):
        """Client-supplied redirect_to must NOT appear in Supabase OAuth URL.

        The view intentionally ignores client-supplied redirect_to to prevent
        open-redirect attacks.  The Supabase redirect_to param must only contain
        the hardcoded /auth/callback path.
        """
        resp = self.client.get(self.url, {"redirect_to": "https://evil.example.com/steal"})
        self.assertEqual(resp.status_code, 302)
        location = resp["Location"]
        qs = parse_qs(urlparse(location).query)
        redirect_to_value = unquote_plus(qs.get("redirect_to", [""])[0])
        # The callback URL must NOT embed the attacker-supplied destination.
        self.assertNotIn("evil.example.com", redirect_to_value)
        # It MUST still point to our own callback.
        self.assertIn("/auth/callback", redirect_to_value)

    # ------------------------------------------------------------------
    # Configuration error — missing SUPABASE_URL
    # ------------------------------------------------------------------

    @override_settings(SUPABASE_URL="")
    def test_missing_supabase_url_returns_503(self):
        """Missing SUPABASE_URL → 503 Service Unavailable."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 503)

    @override_settings(SUPABASE_URL="")
    def test_503_response_has_no_internal_details(self):
        """503 response body must not expose configuration internals."""
        resp = self.client.get(self.url)
        body_text = resp.content.decode()
        self.assertNotIn("SUPABASE_URL", body_text)

    # ------------------------------------------------------------------
    # Wrong HTTP method
    # ------------------------------------------------------------------

    @override_settings(SUPABASE_URL="https://xyzproject.supabase.co")
    def test_post_method_returns_405(self):
        """POST /auth/player/google → 405 Method Not Allowed."""
        resp = self.client.post(self.url)
        self.assertEqual(resp.status_code, 405)
