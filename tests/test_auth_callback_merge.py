"""
Tests for identity merge in GET /auth/callback (grava-1132.3.3).

Covers:
- Fresh Google login (no existing email row) → normal upsert path
- Merge scenario: existing users row with same email but different UID
    → preserve the original UID in the users table
    → redirect succeeds with tokens
    → merge event is logged
- Network error on lookup → 503
- Merge update network error → 503
"""
from unittest.mock import patch, MagicMock, call
from urllib.parse import urlparse, parse_qs

import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")

from django.test import TestCase, Client, override_settings


def _make_token_response(
    user_id="new-google-uid-456",
    email="player@example.com",
    access_token="eyJ.access.token",
    refresh_token="eyJ.refresh.token",
    token_type="bearer",
):
    """Build a mock Supabase token-exchange response for the Google user."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": token_type,
        "expires_in": 3600,
        "user": {
            "id": user_id,
            "email": email,
            "user_metadata": {"full_name": "Test Player", "avatar_url": ""},
        },
    }
    return mock_resp


def _make_lookup_no_existing(supabase_url="https://xyzproject.supabase.co"):
    """Build a mock lookup response: no existing user with this email."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []  # empty list → no existing row
    return mock_resp


def _make_lookup_existing_user(original_uid="original-email-uid-123"):
    """Build a mock lookup response: existing user with a different UID."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {
            "id": original_uid,
            "email": "player@example.com",
            "role": "player",
        }
    ]
    return mock_resp


def _make_upsert_response():
    """Build a mock successful upsert/update response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    return mock_resp


def _make_update_response():
    """Build a mock successful PATCH (merge update) response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    return mock_resp


@override_settings(
    SUPABASE_URL="https://xyzproject.supabase.co",
    SUPABASE_ANON_KEY="anon-key-test",
    SUPABASE_SERVICE_ROLE_KEY="service-role-key-test",
    FRONTEND_URL="https://app.example.com",
    ALLOWED_HOSTS=["testserver", "localhost"],
)
class AuthCallbackMergeTests(TestCase):
    """Tests for identity merge behavior in GET /auth/callback."""

    def setUp(self):
        self.client = Client()
        self.url = "/auth/callback"

    # ------------------------------------------------------------------
    # No merge needed — fresh Google login (no existing email row)
    # ------------------------------------------------------------------

    def test_no_existing_user_performs_normal_upsert(self):
        """When no row with the email exists, perform a normal upsert (no merge)."""
        token_resp = _make_token_response()
        lookup_resp = _make_lookup_no_existing()
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp) as mock_get:
            resp = self.client.get(self.url, {"code": "test-code"})

        self.assertEqual(resp.status_code, 302)
        # lookup should have been called
        mock_get.assert_called_once()
        # upsert should be called with the Google UID (no merge needed)
        self.assertEqual(mock_post.call_count, 2)  # token exchange + upsert
        upsert_call = mock_post.call_args_list[1]
        posted_json = upsert_call[1].get("json") or {}
        self.assertEqual(posted_json.get("id"), "new-google-uid-456")

    # ------------------------------------------------------------------
    # Merge scenario — existing email with different UID
    # ------------------------------------------------------------------

    def test_merge_preserves_original_uid_in_users_table(self):
        """Existing email/password user logs in with Google → original UID preserved."""
        token_resp = _make_token_response(
            user_id="new-google-uid-456",
            email="player@example.com",
        )
        lookup_resp = _make_lookup_existing_user(original_uid="original-email-uid-123")
        update_resp = _make_update_response()

        with patch("auth_ext.views.requests.post", return_value=_make_token_response()) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp) as mock_get:
            # Replace post side_effect to handle token exchange + PATCH merge separately
            pass

        # Full integration test with proper mocking
        with patch("auth_ext.views.requests.post", side_effect=[token_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp) as mock_get, \
             patch("auth_ext.views.requests.patch", return_value=update_resp) as mock_patch:
            resp = self.client.get(self.url, {"code": "test-code"})

        self.assertEqual(resp.status_code, 302)
        # PATCH should have been called to update the existing row (keep original UID)
        mock_patch.assert_called_once()
        patch_call = mock_patch.call_args
        patch_url = patch_call[0][0] if patch_call[0] else patch_call[1].get("url", "")
        # Must target the existing row by its original ID
        self.assertIn("original-email-uid-123", patch_url)

    def test_merge_redirect_succeeds_with_tokens(self):
        """After merge, client still gets a valid redirect with tokens in fragment."""
        token_resp = _make_token_response(
            user_id="new-google-uid-456",
            email="player@example.com",
            access_token="merged.access.token",
            refresh_token="merged.refresh.token",
        )
        lookup_resp = _make_lookup_existing_user(original_uid="original-email-uid-123")
        update_resp = _make_update_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp), \
             patch("auth_ext.views.requests.patch", return_value=update_resp):
            resp = self.client.get(self.url, {"code": "test-code"})

        self.assertEqual(resp.status_code, 302)
        location = resp["Location"]
        parsed = urlparse(location)
        # Tokens must be in fragment
        frag_params = parse_qs(parsed.fragment)
        self.assertEqual(frag_params["access_token"][0], "merged.access.token")
        self.assertEqual(frag_params["refresh_token"][0], "merged.refresh.token")

    def test_merge_does_not_create_duplicate_user_row(self):
        """When merging, the view must NOT call upsert with the new Google UID."""
        token_resp = _make_token_response(
            user_id="new-google-uid-456",
            email="player@example.com",
        )
        lookup_resp = _make_lookup_existing_user(original_uid="original-email-uid-123")
        update_resp = _make_update_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp), \
             patch("auth_ext.views.requests.patch", return_value=update_resp):
            resp = self.client.get(self.url, {"code": "test-code"})

        # post should only be called once (token exchange), NOT again for upsert
        # because merge uses PATCH to update the existing row instead
        self.assertEqual(mock_post.call_count, 1)

    def test_same_provider_login_no_merge_needed(self):
        """If the returning user's UID matches the existing row's UID, no merge occurs."""
        same_uid = "user-uid-same"
        token_resp = _make_token_response(user_id=same_uid, email="player@example.com")
        # Lookup returns a row with the SAME UID → already merged / same provider
        lookup_resp = MagicMock()
        lookup_resp.status_code = 200
        lookup_resp.json.return_value = [{"id": same_uid, "email": "player@example.com"}]
        upsert_resp = _make_upsert_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp, upsert_resp]) as mock_post, \
             patch("auth_ext.views.requests.get", return_value=lookup_resp), \
             patch("auth_ext.views.requests.patch") as mock_patch:
            resp = self.client.get(self.url, {"code": "test-code"})

        self.assertEqual(resp.status_code, 302)
        # No PATCH (merge) should happen when UIDs match
        mock_patch.assert_not_called()

    # ------------------------------------------------------------------
    # Network errors during merge
    # ------------------------------------------------------------------

    def test_network_error_on_lookup_returns_503(self):
        """Network error when looking up existing user by email → 503."""
        import requests as req_lib
        token_resp = _make_token_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp]), \
             patch("auth_ext.views.requests.get", side_effect=req_lib.RequestException("lookup timeout")):
            resp = self.client.get(self.url, {"code": "test-code"})

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        # No internal detail in response
        self.assertNotIn("lookup timeout", str(body))

    def test_network_error_on_merge_update_returns_503(self):
        """Network error when PATCHing the existing row during merge → 503."""
        import requests as req_lib
        token_resp = _make_token_response(user_id="new-google-uid-456")
        lookup_resp = _make_lookup_existing_user(original_uid="original-email-uid-123")

        with patch("auth_ext.views.requests.post", side_effect=[token_resp]), \
             patch("auth_ext.views.requests.get", return_value=lookup_resp), \
             patch("auth_ext.views.requests.patch", side_effect=req_lib.RequestException("patch timeout")):
            resp = self.client.get(self.url, {"code": "test-code"})

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)

    def test_lookup_returns_multiple_rows_uses_first(self):
        """If lookup returns multiple rows (edge case), use the first one for merge."""
        token_resp = _make_token_response(user_id="new-google-uid-456")
        lookup_resp = MagicMock()
        lookup_resp.status_code = 200
        lookup_resp.json.return_value = [
            {"id": "first-uid", "email": "player@example.com"},
            {"id": "second-uid", "email": "player@example.com"},
        ]
        update_resp = _make_update_response()

        with patch("auth_ext.views.requests.post", side_effect=[token_resp]), \
             patch("auth_ext.views.requests.get", return_value=lookup_resp), \
             patch("auth_ext.views.requests.patch", return_value=update_resp) as mock_patch:
            resp = self.client.get(self.url, {"code": "test-code"})

        self.assertEqual(resp.status_code, 302)
        patch_call = mock_patch.call_args
        patch_url = patch_call[0][0] if patch_call[0] else patch_call[1].get("url", "")
        self.assertIn("first-uid", patch_url)
