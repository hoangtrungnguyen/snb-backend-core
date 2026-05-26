"""
Tests for POST /api/slots/{id}/last-minute — last-minute slot push notification
(grava-52bc.4).

Covers all subtasks:
  grava-52bc.4.1 — POST /slots/{id}/last-minute: owner marks slot as last-minute
  grava-52bc.4.2 — Service queries users within 5km of the court
  grava-52bc.4.3 — Distance filter uses Haversine/earth_distance formula via RPC
  grava-52bc.4.4 — FCM multicast to matching users; deep-link {screen: court_detail, court_id, slot_id}
  grava-52bc.4.5 — Rate limit: slot_push_log tracks 1 push per user per slot

All Supabase HTTP calls are mocked — no real network requests.
"""
import json
import uuid
from unittest.mock import patch, MagicMock, call

import pytest
from django.test import TestCase, Client


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OTHER_OWNER_ID = "eeeeeeee-0000-0000-0000-000000000005"
_PLAYER_ID_1 = "11111111-0000-0000-0000-000000000001"
_PLAYER_ID_2 = "22222222-0000-0000-0000-000000000002"
_COURT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_SLOT_ID = "cccccccc-0000-0000-0000-000000000003"

_COURT_LAT = 10.7769
_COURT_LNG = 106.7009

_OPEN_SLOT_ROW = {
    "id": _SLOT_ID,
    "court_id": _COURT_ID,
    "start_at": "2026-06-01T10:00:00Z",
    "end_at": "2026-06-01T12:00:00Z",
    "status": "open",
    "is_owner_slot": False,
    "is_last_minute": False,
    "access_policy": None,
    "max_players": None,
    "blocked_reason": None,
    "created_at": "2026-05-26T00:00:00Z",
    "updated_at": "2026-05-26T00:00:00Z",
}

_LAST_MINUTE_SLOT_ROW = dict(_OPEN_SLOT_ROW, is_last_minute=True)

_COURT_ROW = {
    "id": _COURT_ID,
    "owner_id": _OWNER_ID,
    "name": "Sân Cầu Lông ABC",
    "lat": _COURT_LAT,
    "lng": _COURT_LNG,
}

_OWNER_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}

_PLAYER_PAYLOAD = {
    "sub": _PLAYER_ID_1,
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_NEARBY_USERS = [
    {"id": _PLAYER_ID_1, "fcm_tokens": ["tok_player1_a", "tok_player1_b"]},
    {"id": _PLAYER_ID_2, "fcm_tokens": ["tok_player2"]},
]


def _mock_resp(status_code: int, data):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


def _ok_resp(data):
    return _mock_resp(200, data)


def _created_resp(data):
    return _mock_resp(201, data)


def _err_resp(status=500):
    return _mock_resp(status, {"message": "error"})


# ---------------------------------------------------------------------------
# grava-52bc.4.1 — POST /slots/{id}/last-minute endpoint basics
# ---------------------------------------------------------------------------

class TestLastMinuteEndpointAuth(TestCase):
    """Authentication and authorization for POST /slots/{id}/last-minute."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/slots/{_SLOT_ID}/last-minute"

    def test_no_auth_returns_401(self):
        """No Authorization header → 401."""
        resp = self.client.post(self.url, content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        """Invalid token → 401."""
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self.client.post(
                self.url,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer bad-token",
            )
        self.assertEqual(resp.status_code, 401)

    def test_player_role_returns_403(self):
        """Player (non-owner) → 403."""
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.post(
                self.url,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer player-token",
            )
        self.assertEqual(resp.status_code, 403)

    def test_get_method_not_allowed(self):
        """GET → 405."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.get(
                self.url,
                HTTP_AUTHORIZATION="Bearer owner-token",
            )
        self.assertEqual(resp.status_code, 405)

    def test_slot_not_found_returns_404(self):
        """Slot not found → 404."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get") as mock_get, \
             patch("courts.views.requests.patch"):
            # slot fetch → empty
            mock_get.return_value = _ok_resp([])
            resp = self.client.post(
                self.url,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner-token",
            )
        self.assertEqual(resp.status_code, 404)

    def test_owner_of_different_court_returns_403(self):
        """Owner of a different court → 403."""
        other_court_row = dict(_COURT_ROW, owner_id=_OTHER_OWNER_ID)

        def fake_get(url, **kwargs):
            if "slots" in url:
                return _ok_resp([_OPEN_SLOT_ROW])
            if "courts" in url:
                return _ok_resp([other_court_row])
            return _ok_resp([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=fake_get), \
             patch("courts.views.requests.patch"):
            resp = self.client.post(
                self.url,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner-token",
            )
        self.assertEqual(resp.status_code, 403)


class TestLastMinuteEndpointSuccess(TestCase):
    """Successful POST /slots/{id}/last-minute marks slot and triggers push."""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/slots/{_SLOT_ID}/last-minute"

    def _make_request(self, mock_patch_fcm=None, nearby_users=None, push_log_existing=None):
        """
        Helper: run a valid POST /last-minute request with fully mocked Supabase and FCM.
        """
        if nearby_users is None:
            nearby_users = []
        if push_log_existing is None:
            push_log_existing = []

        rpc_response = _ok_resp(nearby_users)
        slot_patch_response = _ok_resp([_LAST_MINUTE_SLOT_ROW])
        push_log_get_response = _ok_resp(push_log_existing)
        push_log_insert_response = _created_resp([{"id": str(uuid.uuid4())}])

        def fake_get(url, **kwargs):
            params = kwargs.get("params", {})
            if "/rest/v1/slots" in url and "id" in str(params):
                return _ok_resp([_OPEN_SLOT_ROW])
            if "/rest/v1/courts" in url:
                return _ok_resp([_COURT_ROW])
            if "/rest/v1/slot_push_log" in url:
                return push_log_get_response
            return _ok_resp([])

        def fake_post(url, **kwargs):
            if "/rpc/" in url or "nearby" in url.lower():
                return rpc_response
            if "/rest/v1/slot_push_log" in url:
                return push_log_insert_response
            return _created_resp([])

        def fake_patch(url, **kwargs):
            if "/rest/v1/slots" in url:
                return slot_patch_response
            return _ok_resp([])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=fake_get), \
             patch("courts.views.requests.post", side_effect=fake_post), \
             patch("courts.views.requests.patch", side_effect=fake_patch), \
             patch("notifications.service._send_fcm_multicast") as mock_fcm:
            resp = self.client.post(
                self.url,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner-token",
            )
            fcm_calls = mock_fcm.call_count

        return resp, fcm_calls

    def test_returns_200_on_success(self):
        """Successful last-minute mark → 200."""
        resp, _ = self._make_request()
        self.assertEqual(resp.status_code, 200)

    def test_response_contains_slot_data(self):
        """Response includes slot id."""
        resp, _ = self._make_request()
        data = resp.json()
        self.assertIn("id", data)
        self.assertEqual(data["id"], _SLOT_ID)

    def test_is_last_minute_flagged_true(self):
        """Response has is_last_minute = True."""
        resp, _ = self._make_request()
        data = resp.json()
        self.assertIs(data.get("is_last_minute"), True)

    def test_slot_patch_sets_is_last_minute(self):
        """PATCH to slots table must set is_last_minute=True."""
        slot_patch_calls = []

        def fake_get(url, **kwargs):
            if "/rest/v1/slots" in url:
                return _ok_resp([_OPEN_SLOT_ROW])
            if "/rest/v1/courts" in url:
                return _ok_resp([_COURT_ROW])
            if "/rest/v1/slot_push_log" in url:
                return _ok_resp([])
            return _ok_resp([])

        def fake_post(url, **kwargs):
            return _created_resp([])

        def fake_patch(url, **kwargs):
            slot_patch_calls.append((url, kwargs))
            return _ok_resp([_LAST_MINUTE_SLOT_ROW])

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=fake_get), \
             patch("courts.views.requests.post", side_effect=fake_post), \
             patch("courts.views.requests.patch", side_effect=fake_patch), \
             patch("notifications.service._send_fcm_multicast"):
            self.client.post(
                self.url,
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner-token",
            )

        self.assertTrue(len(slot_patch_calls) > 0, "Must call PATCH on slots")
        patch_body = slot_patch_calls[0][1].get("json", {})
        self.assertIs(patch_body.get("is_last_minute"), True,
                      f"PATCH body must have is_last_minute=True, got: {patch_body}")


# ---------------------------------------------------------------------------
# grava-52bc.4.2 — Location-based user query within 5km
# ---------------------------------------------------------------------------

class TestLocationQuery(TestCase):
    """Service queries users within 5km of court (grava-52bc.4.2)."""

    def test_nearby_users_queried_via_rpc(self):
        """
        The implementation must call a Supabase RPC or REST query that
        fetches users near the court's lat/lng.
        """
        from notifications.last_minute import query_nearby_users

        nearby = [{"id": _PLAYER_ID_1, "fcm_tokens": ["tok1"]}]

        with patch("notifications.last_minute.requests.post") as mock_post, \
             patch("notifications.last_minute.requests.get") as mock_get:
            mock_post.return_value = _ok_resp(nearby)
            mock_get.return_value = _ok_resp(nearby)

            result = query_nearby_users(
                court_lat=_COURT_LAT,
                court_lng=_COURT_LNG,
                radius_meters=5000,
            )

        # Either POST (RPC) or GET (REST) must be called
        total_calls = mock_post.call_count + mock_get.call_count
        self.assertGreater(total_calls, 0, "Must make at least one Supabase call")

    def test_nearby_users_returns_list(self):
        """query_nearby_users always returns a list."""
        from notifications.last_minute import query_nearby_users

        with patch("notifications.last_minute.requests.post", return_value=_ok_resp([])), \
             patch("notifications.last_minute.requests.get", return_value=_ok_resp([])):
            result = query_nearby_users(
                court_lat=_COURT_LAT,
                court_lng=_COURT_LNG,
                radius_meters=5000,
            )

        self.assertIsInstance(result, list)

    def test_nearby_users_returns_empty_on_error(self):
        """On Supabase error, returns [] without raising."""
        from notifications.last_minute import query_nearby_users

        with patch("notifications.last_minute.requests.post", return_value=_err_resp(500)), \
             patch("notifications.last_minute.requests.get", return_value=_err_resp(500)):
            result = query_nearby_users(
                court_lat=_COURT_LAT,
                court_lng=_COURT_LNG,
                radius_meters=5000,
            )

        self.assertEqual(result, [])

    def test_nearby_users_returns_empty_on_network_error(self):
        """On network error, returns [] without raising."""
        import requests as req_lib
        from notifications.last_minute import query_nearby_users

        with patch("notifications.last_minute.requests.post",
                   side_effect=req_lib.RequestException("timeout")), \
             patch("notifications.last_minute.requests.get",
                   side_effect=req_lib.RequestException("timeout")):
            result = query_nearby_users(
                court_lat=_COURT_LAT,
                court_lng=_COURT_LNG,
                radius_meters=5000,
            )

        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# grava-52bc.4.3 — Distance filter uses earth_distance / Haversine
# ---------------------------------------------------------------------------

class TestDistanceFilter(TestCase):
    """Distance filter uses Haversine/earth_distance SQL (grava-52bc.4.3)."""

    def test_query_passes_lat_lng_to_supabase(self):
        """The RPC/query call must reference lat and lng values."""
        from notifications.last_minute import query_nearby_users

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            captured["json"] = kwargs.get("json", {})
            return _ok_resp([])

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params", {})
            return _ok_resp([])

        with patch("notifications.last_minute.requests.post", side_effect=fake_post), \
             patch("notifications.last_minute.requests.get", side_effect=fake_get):
            query_nearby_users(
                court_lat=_COURT_LAT,
                court_lng=_COURT_LNG,
                radius_meters=5000,
            )

        # lat and lng must appear somewhere in the call
        all_data = str(captured)
        self.assertIn(str(_COURT_LAT), all_data,
                      "Court lat must be passed to Supabase query")
        self.assertIn(str(_COURT_LNG), all_data,
                      "Court lng must be passed to Supabase query")

    def test_query_passes_radius_5km(self):
        """The radius value (5000m) must appear in the query call."""
        from notifications.last_minute import query_nearby_users

        captured = {}

        def fake_post(url, **kwargs):
            captured["json"] = kwargs.get("json", {})
            return _ok_resp([])

        def fake_get(url, **kwargs):
            captured["params"] = kwargs.get("params", {})
            return _ok_resp([])

        with patch("notifications.last_minute.requests.post", side_effect=fake_post), \
             patch("notifications.last_minute.requests.get", side_effect=fake_get):
            query_nearby_users(
                court_lat=_COURT_LAT,
                court_lng=_COURT_LNG,
                radius_meters=5000,
            )

        all_data = str(captured)
        self.assertIn("5000", all_data,
                      "Radius 5000m must be passed to Supabase query")

    def test_rpc_function_name_contains_nearby(self):
        """RPC or filter function name must reference a location/nearby concept."""
        from notifications.last_minute import query_nearby_users

        captured_urls = []

        def fake_post(url, **kwargs):
            captured_urls.append(url)
            return _ok_resp([])

        def fake_get(url, **kwargs):
            captured_urls.append(url)
            return _ok_resp([])

        with patch("notifications.last_minute.requests.post", side_effect=fake_post), \
             patch("notifications.last_minute.requests.get", side_effect=fake_get):
            query_nearby_users(
                court_lat=_COURT_LAT,
                court_lng=_COURT_LNG,
                radius_meters=5000,
            )

        all_urls = " ".join(captured_urls)
        # URL should have some location/proximity keyword
        self.assertTrue(
            any(kw in all_urls.lower() for kw in
                ["nearby", "location", "rpc", "last_lat", "earth", "haversine", "distance"]),
            f"Expected location-related URL, got: {captured_urls}"
        )


# ---------------------------------------------------------------------------
# grava-52bc.4.4 — FCM multicast with deep-link data
# ---------------------------------------------------------------------------

class TestFCMMulticast(TestCase):
    """FCM multicast sent to matching users with deep-link data (grava-52bc.4.4)."""

    def test_fcm_sent_to_all_nearby_users(self):
        """FCM must be called once with tokens from all nearby users."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [
            {"id": _PLAYER_ID_1, "fcm_tokens": ["tok1", "tok2"]},
            {"id": _PLAYER_ID_2, "fcm_tokens": ["tok3"]},
        ]

        with patch("notifications.last_minute.requests.get", return_value=_ok_resp([])), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        mock_fcm.assert_called_once()
        call_kwargs = mock_fcm.call_args.kwargs
        tokens = call_kwargs.get("tokens") or mock_fcm.call_args.args[0]
        # All tokens from all nearby users must be included
        self.assertIn("tok1", tokens)
        self.assertIn("tok2", tokens)
        self.assertIn("tok3", tokens)

    def test_fcm_data_contains_screen_court_detail(self):
        """FCM data.screen must be 'court_detail' (grava-52bc.4.4)."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [{"id": _PLAYER_ID_1, "fcm_tokens": ["tok1"]}]

        with patch("notifications.last_minute.requests.get", return_value=_ok_resp([])), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        call_kwargs = mock_fcm.call_args.kwargs
        data = call_kwargs.get("data") or mock_fcm.call_args.args[3]
        self.assertEqual(data.get("screen"), "court_detail",
                         f"data.screen must be 'court_detail', got: {data}")

    def test_fcm_data_contains_slot_id(self):
        """FCM data must include slot_id (grava-52bc.4.4)."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [{"id": _PLAYER_ID_1, "fcm_tokens": ["tok1"]}]

        with patch("notifications.last_minute.requests.get", return_value=_ok_resp([])), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        call_kwargs = mock_fcm.call_args.kwargs
        data = call_kwargs.get("data") or mock_fcm.call_args.args[3]
        self.assertEqual(data.get("slot_id"), _SLOT_ID,
                         f"data.slot_id must be {_SLOT_ID}, got: {data}")

    def test_fcm_data_contains_court_id(self):
        """FCM data must include court_id (grava-52bc.4.4)."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [{"id": _PLAYER_ID_1, "fcm_tokens": ["tok1"]}]

        with patch("notifications.last_minute.requests.get", return_value=_ok_resp([])), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        call_kwargs = mock_fcm.call_args.kwargs
        data = call_kwargs.get("data") or mock_fcm.call_args.args[3]
        self.assertEqual(data.get("court_id"), _COURT_ID,
                         f"data.court_id must be {_COURT_ID}, got: {data}")

    def test_fcm_not_called_when_no_nearby_users(self):
        """FCM must not be called when there are no nearby users."""
        from notifications.last_minute import dispatch_last_minute_push

        with patch("notifications.last_minute.requests.get", return_value=_ok_resp([])), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=[],
            )

        mock_fcm.assert_not_called()

    def test_fcm_not_called_when_all_users_have_empty_tokens(self):
        """FCM must not be called when all nearby users have empty token lists."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [
            {"id": _PLAYER_ID_1, "fcm_tokens": []},
            {"id": _PLAYER_ID_2, "fcm_tokens": None},
        ]

        with patch("notifications.last_minute.requests.get", return_value=_ok_resp([])), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        mock_fcm.assert_not_called()


# ---------------------------------------------------------------------------
# grava-52bc.4.5 — Rate limit: slot_push_log (1 push per user per slot)
# ---------------------------------------------------------------------------

class TestRateLimit(TestCase):
    """slot_push_log ensures 1 push per user per slot (grava-52bc.4.5)."""

    def test_already_pushed_user_skipped(self):
        """User with existing push log entry must be excluded from FCM dispatch."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [
            {"id": _PLAYER_ID_1, "fcm_tokens": ["tok1"]},
            {"id": _PLAYER_ID_2, "fcm_tokens": ["tok2"]},
        ]

        # Push log already has an entry for PLAYER_ID_1
        existing_log = [{"slot_id": _SLOT_ID, "user_id": _PLAYER_ID_1}]

        def fake_get(url, **kwargs):
            if "slot_push_log" in url:
                params = kwargs.get("params", {})
                user_param = str(params.get("user_id", ""))
                if _PLAYER_ID_1 in user_param:
                    return _ok_resp([existing_log[0]])
                return _ok_resp([])
            return _ok_resp([])

        captured_tokens = []

        def capture_fcm(tokens, **kwargs):
            captured_tokens.extend(tokens)

        with patch("notifications.last_minute.requests.get", side_effect=fake_get), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast", side_effect=capture_fcm):
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        # PLAYER_ID_1's token should be excluded (already pushed)
        self.assertNotIn("tok1", captured_tokens,
                         "Token for already-pushed user must be excluded")
        # PLAYER_ID_2's token should still be included
        self.assertIn("tok2", captured_tokens,
                      "Token for new user must be included")

    def test_push_log_entry_created_after_push(self):
        """After FCM dispatch, a slot_push_log row must be inserted for each user."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [
            {"id": _PLAYER_ID_1, "fcm_tokens": ["tok1"]},
        ]

        inserted_logs = []

        def fake_get(url, **kwargs):
            if "slot_push_log" in url:
                return _ok_resp([])  # no existing log
            return _ok_resp([])

        def fake_post(url, **kwargs):
            if "slot_push_log" in url:
                inserted_logs.append(kwargs.get("json", {}))
                return _created_resp([{"id": str(uuid.uuid4())}])
            return _created_resp([])

        with patch("notifications.last_minute.requests.get", side_effect=fake_get), \
             patch("notifications.last_minute.requests.post", side_effect=fake_post), \
             patch("notifications.last_minute._send_fcm_multicast"):
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        self.assertTrue(len(inserted_logs) > 0, "slot_push_log must be inserted")
        log_entry = inserted_logs[0]
        self.assertEqual(log_entry.get("slot_id"), _SLOT_ID)
        self.assertEqual(log_entry.get("user_id"), _PLAYER_ID_1)

    def test_push_log_not_inserted_when_no_push_sent(self):
        """If user has no FCM tokens, slot_push_log must not be inserted."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [
            {"id": _PLAYER_ID_1, "fcm_tokens": []},  # no tokens
        ]

        inserted_logs = []

        def fake_get(url, **kwargs):
            if "slot_push_log" in url:
                return _ok_resp([])
            return _ok_resp([])

        def fake_post(url, **kwargs):
            if "slot_push_log" in url:
                inserted_logs.append(kwargs.get("json", {}))
                return _created_resp([{"id": str(uuid.uuid4())}])
            return _created_resp([])

        with patch("notifications.last_minute.requests.get", side_effect=fake_get), \
             patch("notifications.last_minute.requests.post", side_effect=fake_post), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        mock_fcm.assert_not_called()
        self.assertEqual(len(inserted_logs), 0,
                         "slot_push_log must NOT be inserted when no tokens available")

    def test_all_users_already_pushed_no_fcm(self):
        """When all nearby users already have push logs, FCM must not be called."""
        from notifications.last_minute import dispatch_last_minute_push

        nearby_users = [
            {"id": _PLAYER_ID_1, "fcm_tokens": ["tok1"]},
            {"id": _PLAYER_ID_2, "fcm_tokens": ["tok2"]},
        ]

        def fake_get(url, **kwargs):
            if "slot_push_log" in url:
                # Both users already have entries
                return _ok_resp([{"slot_id": _SLOT_ID, "user_id": "some_id"}])
            return _ok_resp([])

        with patch("notifications.last_minute.requests.get", side_effect=fake_get), \
             patch("notifications.last_minute.requests.post", return_value=_created_resp([])), \
             patch("notifications.last_minute._send_fcm_multicast") as mock_fcm:
            dispatch_last_minute_push(
                slot_id=_SLOT_ID,
                court_id=_COURT_ID,
                court_name="Test Court",
                nearby_users=nearby_users,
            )

        mock_fcm.assert_not_called()


# ---------------------------------------------------------------------------
# grava-52bc.4.5 — Migration 0015 for slot_push_log table
# ---------------------------------------------------------------------------

class TestMigration0015SlotPushLog(TestCase):
    """Migration 0015 creates slot_push_log table (grava-52bc.4.5)."""

    def test_migration_file_exists(self):
        """0015_slot_push_log.py must exist in alembic/versions/."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0015_slot_push_log.py")
        assert os.path.isfile(path), "0015_slot_push_log.py missing from alembic/versions/"

    def test_migration_has_revision_0015(self):
        """Migration must declare revision = '0015'."""
        import importlib.util
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0015_slot_push_log.py")
        spec = importlib.util.spec_from_file_location("migration_0015", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "0015"

    def test_migration_creates_slot_push_log_table(self):
        """upgrade() must create slot_push_log table or reference it."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0015_slot_push_log.py")
        with open(path) as f:
            src = f.read()
        assert "slot_push_log" in src, "0015 migration must reference slot_push_log"

    def test_migration_has_slot_id_and_user_id_columns(self):
        """The migration must define slot_id and user_id columns."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0015_slot_push_log.py")
        with open(path) as f:
            src = f.read()
        assert "slot_id" in src, "0015 migration must define slot_id column"
        assert "user_id" in src, "0015 migration must define user_id column"

    def test_migration_has_pushed_at_column(self):
        """The migration must define pushed_at column."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0015_slot_push_log.py")
        with open(path) as f:
            src = f.read()
        assert "pushed_at" in src, "0015 migration must define pushed_at column"

    def test_migration_upgrade_callable(self):
        """upgrade() and downgrade() must be callable."""
        import importlib.util
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0015_slot_push_log.py")
        spec = importlib.util.spec_from_file_location("migration_0015", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert callable(mod.upgrade)
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# URL registration test
# ---------------------------------------------------------------------------

class TestLastMinuteURL(TestCase):
    """POST /api/slots/{id}/last-minute must be registered in URL conf."""

    def test_url_registered(self):
        """The last-minute URL must be resolvable in Django's URL conf."""
        from django.urls import resolve, reverse, NoReverseMatch

        try:
            url = reverse("slot-last-minute", kwargs={"slot_id": _SLOT_ID})
            self.assertIn("last-minute", url)
        except NoReverseMatch:
            # Also acceptable: URL resolves but name differs
            from django.urls import resolve
            try:
                match = resolve(f"/api/slots/{_SLOT_ID}/last-minute")
                self.assertIsNotNone(match)
            except Exception:
                self.fail(
                    f"/api/slots/{_SLOT_ID}/last-minute must be registered in URL conf"
                )
