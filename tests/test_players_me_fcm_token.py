"""
Tests for FCM device token registration endpoints.

POST /api/players/me/fcm-token  — registers a device token
DELETE /api/players/me/fcm-token — removes a device token on logout

Acceptance criteria (grava-52bc.1.1 / grava-52bc.1.2):
- POST adds the token to users.fcm_tokens[] (deduplication handled by Supabase array_append)
- POST is idempotent: registering the same token twice does not duplicate it
- POST requires {"token": "<non-empty string>"}
- DELETE requires {"token": "<non-empty string>"}
- DELETE returns 204 No Content on success
- Both endpoints require authenticated player role
- Missing/invalid JSON body → 400
- Network failure → 503
"""
import json
import requests as req_lib
from unittest.mock import patch, MagicMock, call

from django.test import TestCase, Client

_PLAYER_PAYLOAD = {
    "sub": "550e8400-e29b-41d4-a716-446655440000",
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OWNER_PAYLOAD = {
    "sub": "550e8400-e29b-41d4-a716-446655440000",
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_USER_ID = "550e8400-e29b-41d4-a716-446655440000"
_FCM_TOKEN = "fCm_ToKeN_abc123"


def _mock_patch_ok():
    m = MagicMock()
    m.status_code = 204
    return m


def _mock_patch_fail():
    m = MagicMock()
    m.status_code = 500
    return m


class PlayersFcmTokenPostTests(TestCase):
    """Tests for POST /api/players/me/fcm-token."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/players/me/fcm-token"
        self.token = "eyJ.valid.token"

    def _post(self, body=None, token=None, raw=None):
        headers = {}
        if token is not False:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token or self.token}"
        if raw is not None:
            return self.client.post(
                self.url,
                data=raw,
                content_type="application/json",
                **headers,
            )
        return self.client.post(
            self.url,
            data=json.dumps(body) if body is not None else "",
            content_type="application/json",
            **headers,
        )

    # ------------------------------------------------------------------
    # Authentication / authorisation
    # ------------------------------------------------------------------

    def test_no_auth_returns_401(self):
        resp = self._post({"token": _FCM_TOKEN}, token=False)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post({"token": _FCM_TOKEN}, token="bad.token")
        self.assertIn(resp.status_code, [401, 403])

    def test_owner_role_returns_403(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_missing_token_field_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post({})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_empty_token_value_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post({"token": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_non_string_token_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post({"token": 12345})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_invalid_json_body_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post(raw="not-json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_register_token_returns_200(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_patch_ok()):
            resp = self._post({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 200)

    def test_register_token_calls_supabase_rpc(self):
        """POST calls the Supabase RPC to append the token."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_patch_ok()) as mock_post:
            self._post({"token": _FCM_TOKEN})

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        # URL should reference rpc/register_fcm_token or array_append via RPC
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        body = call_args.kwargs.get("json") or {}
        # Either RPC URL or direct PATCH — either way token and user_id must appear somewhere
        call_str = str(call_args)
        self.assertIn(_FCM_TOKEN, call_str)

    def test_supabase_failure_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_patch_fail()):
            resp = self._post({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 503)

    def test_network_error_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post",
                   side_effect=req_lib.RequestException("timeout")):
            resp = self._post({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn("timeout", json.dumps(body))

    # ------------------------------------------------------------------
    # HTTP method guard
    # ------------------------------------------------------------------

    def test_get_method_not_allowed(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(resp.status_code, 405)


class PlayersFcmTokenDeleteTests(TestCase):
    """Tests for DELETE /api/players/me/fcm-token."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/players/me/fcm-token"
        self.token = "eyJ.valid.token"

    def _delete(self, body=None, token=None, raw=None):
        headers = {}
        if token is not False:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token or self.token}"
        if raw is not None:
            return self.client.delete(
                self.url,
                data=raw,
                content_type="application/json",
                **headers,
            )
        return self.client.delete(
            self.url,
            data=json.dumps(body) if body is not None else "",
            content_type="application/json",
            **headers,
        )

    # ------------------------------------------------------------------
    # Authentication / authorisation
    # ------------------------------------------------------------------

    def test_no_auth_returns_401(self):
        resp = self._delete({"token": _FCM_TOKEN}, token=False)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._delete({"token": _FCM_TOKEN}, token="bad.token")
        self.assertIn(resp.status_code, [401, 403])

    def test_owner_role_returns_403(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._delete({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_missing_token_field_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._delete({})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_empty_token_value_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._delete({"token": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_non_string_token_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._delete({"token": 12345})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_invalid_json_body_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._delete(raw="not-json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_deregister_token_returns_204(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_patch_ok()):
            resp = self._delete({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 204)

    def test_deregister_calls_supabase_rpc(self):
        """DELETE calls the Supabase RPC to remove the token."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_patch_ok()) as mock_post:
            self._delete({"token": _FCM_TOKEN})

        mock_post.assert_called_once()
        call_str = str(mock_post.call_args)
        self.assertIn(_FCM_TOKEN, call_str)

    def test_supabase_failure_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_patch_fail()):
            resp = self._delete({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 503)

    def test_network_error_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post",
                   side_effect=req_lib.RequestException("timeout")):
            resp = self._delete({"token": _FCM_TOKEN})
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn("timeout", json.dumps(body))

    # ------------------------------------------------------------------
    # HTTP method guard
    # ------------------------------------------------------------------

    def test_get_method_not_allowed(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(resp.status_code, 405)
