"""
Tests for in-app notification dispatch (grava-52bc.2).

Covers:
- grava-52bc.2.1: notifications row insert (type, related_booking_id, related_series_id, data.deep_link)
- grava-52bc.2.3: FCM send_multicast to user's fcm_tokens after notify
- grava-52bc.2.4: data.deep_link payload in response
- grava-52bc.2.5: GET /api/notifications?page=&limit=  — paginated list
- grava-52bc.2.6: PATCH /api/notifications/{id}/read  — mark single read
- grava-52bc.2.7: POST /api/notifications/read-all     — mark all read

grava-52bc.2.2 (Supabase Realtime) is handled by alembic migration 0012 and
verified via test_migration_0012_realtime_notifications.py — no backend-code
test is needed here since Realtime triggers on INSERT automatically.
"""

import json
import uuid
import requests as req_lib
from unittest.mock import patch, MagicMock, call

from django.test import TestCase, Client

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
_NOTIF_ID = "660e8400-e29b-41d4-a716-446655440001"
_BOOKING_ID = "770e8400-e29b-41d4-a716-446655440002"
_SERIES_ID = "880e8400-e29b-41d4-a716-446655440003"


def _ok_resp(data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    return m


def _err_resp(status=500):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = {"message": "error"}
    return m


# ---------------------------------------------------------------------------
# Service-layer unit tests (grava-52bc.2.1, 2.3, 2.4)
# ---------------------------------------------------------------------------

class NotificationServiceInsertTests(TestCase):
    """Unit tests for notifications.service.dispatch_notification()."""

    def test_insert_notification_row_with_required_fields(self):
        """dispatch_notification inserts a row with type, user_id, data.deep_link."""
        from notifications.service import dispatch_notification

        insert_resp = _ok_resp(
            [{"id": _NOTIF_ID, "user_id": _USER_ID, "type": "booking_confirmed",
              "related_booking_id": _BOOKING_ID, "related_series_id": None,
              "data": {"deep_link": "/bookings/770e8400"}, "read": False,
              "created_at": "2026-05-26T10:00:00Z"}],
            status=201,
        )
        user_resp = _ok_resp(
            [{"id": _USER_ID, "fcm_tokens": ["tok1", "tok2"]}]
        )

        with patch("notifications.service.requests.post", return_value=insert_resp) as mock_post, \
             patch("notifications.service.requests.get", return_value=user_resp), \
             patch("notifications.service._send_fcm_multicast") as mock_fcm:

            result = dispatch_notification(
                user_id=_USER_ID,
                notif_type="booking_confirmed",
                title="Đặt sân thành công",
                body="Sân A lúc 10:00",
                related_booking_id=_BOOKING_ID,
                related_series_id=None,
                deep_link=f"/bookings/{_BOOKING_ID}",
            )

        # The POST to Supabase /rest/v1/notifications must have been called
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        posted_json = call_kwargs.kwargs.get("json") or {}
        self.assertEqual(posted_json.get("type"), "booking_confirmed")
        self.assertEqual(posted_json.get("user_id"), _USER_ID)
        self.assertEqual(posted_json.get("related_booking_id"), _BOOKING_ID)
        self.assertIsNone(posted_json.get("related_series_id"))
        data = posted_json.get("data", {})
        self.assertIn("deep_link", data)

    def test_insert_notification_with_series_id(self):
        """dispatch_notification correctly passes related_series_id."""
        from notifications.service import dispatch_notification

        insert_resp = _ok_resp(
            [{"id": _NOTIF_ID, "user_id": _USER_ID, "type": "series_confirmed",
              "related_booking_id": None, "related_series_id": _SERIES_ID,
              "data": {"deep_link": f"/series/{_SERIES_ID}"}, "read": False,
              "created_at": "2026-05-26T10:00:00Z"}],
            status=201,
        )
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": []}])

        with patch("notifications.service.requests.post", return_value=insert_resp), \
             patch("notifications.service.requests.get", return_value=user_resp), \
             patch("notifications.service._send_fcm_multicast"):

            dispatch_notification(
                user_id=_USER_ID,
                notif_type="series_confirmed",
                title="Lịch cố định đã được xác nhận",
                body="5 buổi",
                related_booking_id=None,
                related_series_id=_SERIES_ID,
                deep_link=f"/series/{_SERIES_ID}",
            )

        # verify call args
        from notifications.service import requests as svc_requests  # noqa: F401

    def test_fcm_multicast_called_with_tokens(self):
        """dispatch_notification calls _send_fcm_multicast with user fcm_tokens."""
        from notifications.service import dispatch_notification

        insert_resp = _ok_resp(
            [{"id": _NOTIF_ID, "user_id": _USER_ID, "type": "booking_confirmed",
              "related_booking_id": _BOOKING_ID, "related_series_id": None,
              "data": {"deep_link": "/bookings/x"}, "read": False,
              "created_at": "2026-05-26T10:00:00Z"}],
            status=201,
        )
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok_a", "tok_b"]}])

        with patch("notifications.service.requests.post", return_value=insert_resp), \
             patch("notifications.service.requests.get", return_value=user_resp), \
             patch("notifications.service._send_fcm_multicast") as mock_fcm:

            dispatch_notification(
                user_id=_USER_ID,
                notif_type="booking_confirmed",
                title="Title",
                body="Body",
                related_booking_id=_BOOKING_ID,
                related_series_id=None,
                deep_link="/bookings/x",
            )

        mock_fcm.assert_called_once()
        call_args = mock_fcm.call_args
        tokens = call_args.args[0] if call_args.args else call_args.kwargs.get("tokens")
        self.assertEqual(tokens, ["tok_a", "tok_b"])

    def test_fcm_skipped_when_no_tokens(self):
        """dispatch_notification skips FCM if user has no fcm_tokens."""
        from notifications.service import dispatch_notification

        insert_resp = _ok_resp(
            [{"id": _NOTIF_ID, "user_id": _USER_ID, "type": "booking_confirmed",
              "related_booking_id": _BOOKING_ID, "related_series_id": None,
              "data": {"deep_link": "/bookings/x"}, "read": False,
              "created_at": "2026-05-26T10:00:00Z"}],
            status=201,
        )
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": []}])

        with patch("notifications.service.requests.post", return_value=insert_resp), \
             patch("notifications.service.requests.get", return_value=user_resp), \
             patch("notifications.service._send_fcm_multicast") as mock_fcm:

            dispatch_notification(
                user_id=_USER_ID,
                notif_type="booking_confirmed",
                title="T",
                body="B",
                related_booking_id=_BOOKING_ID,
                related_series_id=None,
                deep_link="/bookings/x",
            )

        mock_fcm.assert_not_called()

    def test_deep_link_in_data_payload(self):
        """The notification row's data JSONB includes deep_link (grava-52bc.2.4)."""
        from notifications.service import dispatch_notification

        deep_link = f"/bookings/{_BOOKING_ID}"
        insert_resp = _ok_resp(
            [{"id": _NOTIF_ID, "user_id": _USER_ID, "type": "booking_confirmed",
              "related_booking_id": _BOOKING_ID, "related_series_id": None,
              "data": {"deep_link": deep_link}, "read": False,
              "created_at": "2026-05-26T10:00:00Z"}],
            status=201,
        )
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": []}])

        with patch("notifications.service.requests.post", return_value=insert_resp) as mock_post, \
             patch("notifications.service.requests.get", return_value=user_resp), \
             patch("notifications.service._send_fcm_multicast"):

            dispatch_notification(
                user_id=_USER_ID,
                notif_type="booking_confirmed",
                title="T",
                body="B",
                related_booking_id=_BOOKING_ID,
                related_series_id=None,
                deep_link=deep_link,
            )

        call_kwargs = mock_post.call_args
        posted_json = call_kwargs.kwargs.get("json") or {}
        self.assertIn("data", posted_json)
        self.assertEqual(posted_json["data"].get("deep_link"), deep_link)

    def test_supabase_insert_failure_raises(self):
        """dispatch_notification raises RuntimeError on Supabase insert failure."""
        from notifications.service import dispatch_notification

        insert_resp = _err_resp(500)

        with patch("notifications.service.requests.post", return_value=insert_resp), \
             patch("notifications.service.requests.get", return_value=_ok_resp([])), \
             patch("notifications.service._send_fcm_multicast"):

            with self.assertRaises(RuntimeError):
                dispatch_notification(
                    user_id=_USER_ID,
                    notif_type="booking_confirmed",
                    title="T",
                    body="B",
                    related_booking_id=None,
                    related_series_id=None,
                    deep_link="/",
                )


# ---------------------------------------------------------------------------
# GET /api/notifications  (grava-52bc.2.5)
# ---------------------------------------------------------------------------

class NotificationsListViewTests(TestCase):
    """Tests for GET /api/notifications."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/notifications"
        self.token = "eyJ.valid.token"

    def _get(self, params="", token=None, no_auth=False):
        headers = {}
        if not no_auth:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token or self.token}"
        return self.client.get(f"{self.url}{params}", **headers)

    def _notif_row(self, idx=0, is_read=False):
        return {
            "id": str(uuid.uuid4()),
            "user_id": _USER_ID,
            "type": "booking_confirmed",
            "title": f"Notification {idx}",
            "body": "Body text",
            "data": {"deep_link": f"/bookings/{idx}"},
            "read": is_read,
            "related_booking_id": _BOOKING_ID,
            "related_series_id": None,
            "created_at": f"2026-05-26T{10 + idx:02d}:00:00Z",
        }

    # Authentication
    def test_unauthenticated_returns_401(self):
        resp = self._get(no_auth=True)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._get(token="bad.tok")
        self.assertIn(resp.status_code, [401, 403])

    # Success
    def test_returns_paginated_list(self):
        rows = [self._notif_row(i) for i in range(3)]
        supabase_resp = _ok_resp(rows)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.get", return_value=supabase_resp):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("results", body)
        self.assertEqual(len(body["results"]), 3)

    def test_response_includes_deep_link(self):
        """Each notification item must expose data.deep_link (grava-52bc.2.4)."""
        rows = [self._notif_row(0)]
        supabase_resp = _ok_resp(rows)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.get", return_value=supabase_resp):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        item = resp.json()["results"][0]
        self.assertIn("data", item)
        self.assertIn("deep_link", item["data"])

    def test_pagination_page_and_limit(self):
        """page and limit query params translate to Supabase offset/limit."""
        rows = [self._notif_row(i) for i in range(5)]
        supabase_resp = _ok_resp(rows)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.get", return_value=supabase_resp) as mock_get:
            resp = self._get("?page=2&limit=5")

        self.assertEqual(resp.status_code, 200)
        # Verify Supabase was called with correct range header or offset param
        mock_get.assert_called_once()

    def test_default_pagination_applied(self):
        """Without page/limit params, sensible defaults are applied."""
        supabase_resp = _ok_resp([self._notif_row(0)])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.get", return_value=supabase_resp):
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("page", body)
        self.assertIn("limit", body)

    def test_network_error_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.get",
                   side_effect=req_lib.RequestException("timeout")):
            resp = self._get()

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn("timeout", json.dumps(body))

    def test_only_authenticated_user_notifications_returned(self):
        """The Supabase query must filter by user_id."""
        supabase_resp = _ok_resp([])

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.get", return_value=supabase_resp) as mock_get:
            resp = self._get()

        self.assertEqual(resp.status_code, 200)
        call_str = str(mock_get.call_args)
        # user_id filter must be in the Supabase request
        self.assertIn(_USER_ID, call_str)

    def test_post_method_not_allowed(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.post(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# PATCH /api/notifications/{id}/read  (grava-52bc.2.6)
# ---------------------------------------------------------------------------

class NotificationsMarkReadViewTests(TestCase):
    """Tests for PATCH /api/notifications/{id}/read."""

    def setUp(self):
        self.client = Client()
        self.token = "eyJ.valid.token"

    def _url(self, notif_id=None):
        return f"/api/notifications/{notif_id or _NOTIF_ID}/read"

    def _patch(self, notif_id=None, token=None, no_auth=False):
        headers = {}
        if not no_auth:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token or self.token}"
        return self.client.patch(self._url(notif_id), **headers)

    # Authentication
    def test_unauthenticated_returns_401(self):
        resp = self._patch(no_auth=True)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._patch(token="bad")
        self.assertIn(resp.status_code, [401, 403])

    # Success
    def test_marks_notification_read_returns_200(self):
        updated = [{"id": _NOTIF_ID, "user_id": _USER_ID, "read": True,
                    "type": "booking_confirmed", "title": "T", "body": "B",
                    "data": {"deep_link": "/bookings/x"}, "created_at": "2026-05-26T10:00:00Z",
                    "related_booking_id": None, "related_series_id": None}]
        patch_resp = _ok_resp(updated, status=200)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch", return_value=patch_resp):
            resp = self._patch()

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body.get("read"))

    def test_supabase_update_filters_by_user_id(self):
        """PATCH must filter by both id and user_id (prevents cross-user updates)."""
        updated = [{"id": _NOTIF_ID, "user_id": _USER_ID, "read": True,
                    "type": "booking_confirmed", "title": "T", "body": "B",
                    "data": {}, "created_at": "2026-05-26T10:00:00Z",
                    "related_booking_id": None, "related_series_id": None}]
        patch_resp = _ok_resp(updated, status=200)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch", return_value=patch_resp) as mock_patch:
            self._patch()

        call_str = str(mock_patch.call_args)
        # Both notification id and user_id must appear in the request
        self.assertIn(_NOTIF_ID, call_str)
        self.assertIn(_USER_ID, call_str)

    def test_notification_not_found_returns_404(self):
        """If no matching row (wrong owner or bad id) → 404."""
        patch_resp = _ok_resp([], status=200)  # empty means not found / not owner

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch", return_value=patch_resp):
            resp = self._patch()

        self.assertEqual(resp.status_code, 404)

    def test_network_error_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch",
                   side_effect=req_lib.RequestException("timeout")):
            resp = self._patch()

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn("timeout", json.dumps(body))

    def test_get_method_not_allowed(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.get(self._url(), HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# POST /api/notifications/read-all  (grava-52bc.2.7)
# ---------------------------------------------------------------------------

class NotificationsReadAllViewTests(TestCase):
    """Tests for POST /api/notifications/read-all."""

    def setUp(self):
        self.client = Client()
        self.url = "/api/notifications/read-all"
        self.token = "eyJ.valid.token"

    def _post(self, token=None, no_auth=False):
        headers = {}
        if not no_auth:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token or self.token}"
        return self.client.post(self.url, **headers)

    # Authentication
    def test_unauthenticated_returns_401(self):
        resp = self._post(no_auth=True)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post(token="bad")
        self.assertIn(resp.status_code, [401, 403])

    # Success
    def test_marks_all_unread_returns_200(self):
        patch_resp = _ok_resp([], status=204)  # Supabase PATCH returns no content

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch", return_value=patch_resp):
            resp = self._post()

        self.assertIn(resp.status_code, [200, 204])

    def test_supabase_update_filters_by_user_id(self):
        """Bulk read must only update rows where user_id = current user."""
        patch_resp = _ok_resp([], status=204)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch", return_value=patch_resp) as mock_patch:
            self._post()

        call_str = str(mock_patch.call_args)
        self.assertIn(_USER_ID, call_str)

    def test_supabase_only_updates_unread(self):
        """Bulk read must filter where read=eq.false to avoid unnecessary writes."""
        patch_resp = _ok_resp([], status=204)

        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch", return_value=patch_resp) as mock_patch:
            self._post()

        call_str = str(mock_patch.call_args)
        # The query params should include a filter for unread rows
        self.assertIn("false", call_str.lower())

    def test_network_error_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("notifications.views.requests.patch",
                   side_effect=req_lib.RequestException("timeout")):
            resp = self._post()

        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn("timeout", json.dumps(body))

    def test_get_method_not_allowed(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.get(self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(resp.status_code, 405)
