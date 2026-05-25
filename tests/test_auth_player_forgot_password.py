"""
Tests for POST /auth/player/forgot-password endpoint.

Mocks the Supabase HTTP call — no real network requests are made.
The endpoint must always return HTTP 200 regardless of Supabase response
to prevent user enumeration attacks.
"""
import json
from unittest.mock import patch, MagicMock

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")

from django.test import TestCase, Client


class PlayerForgotPasswordViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.url = "/auth/player/forgot-password"

    def test_valid_email_returns_200_with_message(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(self.url, data=json.dumps({"email": "player@example.com"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("message"), "If that email exists, a reset link has been sent")

    def test_unknown_email_supabase_404_still_returns_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("auth_ext.views.requests.post", return_value=mock_resp):
            resp = self.client.post(self.url, data=json.dumps({"email": "nobody@example.com"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_supabase_network_error_still_returns_200(self):
        import requests as req_lib
        with patch("auth_ext.views.requests.post", side_effect=req_lib.RequestException("timeout")):
            resp = self.client.post(self.url, data=json.dumps({"email": "player@example.com"}), content_type="application/json")
        self.assertEqual(resp.status_code, 200)

    def test_calls_supabase_recover_endpoint_with_email_and_redirect_to(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        app_base_url = "https://api.sportbuddies.com"
        with patch("auth_ext.views.requests.post", return_value=mock_resp) as mock_post:
            with self.settings(SUPABASE_URL="https://proj.supabase.co", SUPABASE_ANON_KEY="test-anon-key", APP_BASE_URL=app_base_url):
                self.client.post(self.url, data=json.dumps({"email": "player@example.com"}), content_type="application/json")
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        called_url = call_args[0][0] if call_args[0] else call_args[1].get("url", "")
        self.assertIn("/auth/v1/recover", called_url)
        posted_json = call_args[1].get("json") or {}
        self.assertEqual(posted_json.get("email"), "player@example.com")
        self.assertEqual(posted_json.get("redirect_to"), f"{app_base_url}/auth/callback?type=recovery")

    def test_missing_email_returns_400(self):
        with patch("auth_ext.views.requests.post") as mock_post:
            resp = self.client.post(self.url, data=json.dumps({}), content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        mock_post.assert_not_called()

    def test_non_json_body_returns_400(self):
        resp = self.client.post(self.url, data="not-json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_wrong_http_method_returns_405(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)
