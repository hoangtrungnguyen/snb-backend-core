"""
Tests for POST /api/slots/{id}/participants (BCORE-304).

Court owner adds a player to a slot by roster — bypassing the join-request flow.
All Supabase HTTP calls are mocked; no real network requests.
"""
import json
from unittest.mock import patch, MagicMock
import requests as _requests_lib

from django.test import TestCase, Client

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-0000-0000-0000-000000000001"
_OTHER_OWNER_ID = "eeeeeeee-0000-0000-0000-000000000099"
_COURT_ID = "bbbbbbbb-0000-0000-0000-000000000002"
_SLOT_ID = "cccccccc-0000-0000-0000-000000000003"
_CUSTOMER_ID = "dddddddd-0000-0000-0000-000000000004"
_PARTICIPANT_ID = "ffffffff-0000-0000-0000-000000000005"

_OWNER_PAYLOAD = {
    "sub": _OWNER_ID,
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}
_PLAYER_PAYLOAD = {
    "sub": "pppppppp-0000-0000-0000-000000000006",
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_SLOT_ROW = {"id": _SLOT_ID, "court_id": _COURT_ID, "max_players": 10}
_SLOT_ROW_NO_MAX = dict(_SLOT_ROW, max_players=None)
_COURT_ROW = {"id": _COURT_ID, "owner_id": _OWNER_ID}
_CUSTOMER_ROW = {"id": _CUSTOMER_ID, "full_name": "Nguyen Van A", "phone": "0901234567"}
_PARTICIPANT_ROW = {
    "id": _PARTICIPANT_ID,
    "slot_id": _SLOT_ID,
    "user_id": _CUSTOMER_ID,
    "payment_status": "unpaid",
    "payment_method": None,
    "joined_at": "2026-05-30T10:00:00Z",
}


def _mock_resp(status_code: int, data):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = data
    return m


def _ok(data):
    return _mock_resp(200, data)


def _created(data):
    return _mock_resp(201, data)


def _empty():
    return _ok([])


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class OwnerAddParticipantTests(TestCase):
    """POST /api/slots/{id}/participants"""

    def setUp(self):
        self.client = Client()
        self.url = f"/api/slots/{_SLOT_ID}/participants"

    def _post(self, body, token="owner.jwt", auth=True):
        kwargs = {
            "data": json.dumps(body),
            "content_type": "application/json",
        }
        if auth:
            kwargs["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        return self.client.post(self.url, **kwargs)

    # ------------------------------------------------------------------
    # Authentication / authorisation
    # ------------------------------------------------------------------

    def test_invalid_token_returns_401(self):
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post({"user_id": _CUSTOMER_ID})
        self.assertEqual(resp.status_code, 401)

    def test_missing_auth_header_returns_401(self):
        resp = self._post({"user_id": _CUSTOMER_ID}, auth=False)
        self.assertEqual(resp.status_code, 401)

    def test_non_owner_role_returns_403(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post({"user_id": _CUSTOMER_ID})
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Body validation
    # ------------------------------------------------------------------

    def test_empty_body_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post({})
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self.client.post(
                self.url,
                data="not json",
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer owner.jwt",
            )
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # Success — add by user_id
    # ------------------------------------------------------------------

    def test_add_by_user_id_returns_201(self):
        """Happy path: user_id provided, player not yet in slot, under capacity."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([_SLOT_ROW]),
                 _ok([_COURT_ROW]),
                 _ok([_CUSTOMER_ROW]),
                 _empty(),           # no duplicate
                 _ok([]),            # count participants (0)
             ]), \
             patch("courts.views.requests.post", return_value=_created([_PARTICIPANT_ROW])):
            resp = self._post({"user_id": _CUSTOMER_ID})

        data = resp.json()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(data["participant"]["user_id"], _CUSTOMER_ID)
        self.assertEqual(data["participant"]["payment_status"], "unpaid")
        self.assertNotIn("warning", data)

    def test_add_by_user_id_over_capacity_returns_warning(self):
        """Slot at max capacity → 201 with warning=over_capacity."""
        slot = dict(_SLOT_ROW, max_players=2)
        existing = [{"id": "x"}, {"id": "y"}]

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([slot]),
                 _ok([_COURT_ROW]),
                 _ok([_CUSTOMER_ROW]),
                 _empty(),
                 _ok(existing),
             ]), \
             patch("courts.views.requests.post", return_value=_created([_PARTICIPANT_ROW])):
            resp = self._post({"user_id": _CUSTOMER_ID})

        data = resp.json()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(data.get("warning"), "over_capacity")

    def test_no_max_players_no_capacity_check(self):
        """max_players=NULL → no capacity query, no warning."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([_SLOT_ROW_NO_MAX]),
                 _ok([_COURT_ROW]),
                 _ok([_CUSTOMER_ROW]),
                 _empty(),
                 # no count call expected (max_players is None)
             ]), \
             patch("courts.views.requests.post", return_value=_created([_PARTICIPANT_ROW])):
            resp = self._post({"user_id": _CUSTOMER_ID})

        data = resp.json()
        self.assertEqual(resp.status_code, 201)
        self.assertNotIn("warning", data)

    # ------------------------------------------------------------------
    # Success — add by name/phone (upsert path)
    # ------------------------------------------------------------------

    def test_add_by_phone_existing_customer(self):
        """Phone matches existing customer → no upsert needed."""
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([_SLOT_ROW_NO_MAX]),
                 _ok([_COURT_ROW]),
                 _ok([_CUSTOMER_ROW]),  # search by phone → found
                 _empty(),              # no duplicate
             ]), \
             patch("courts.views.requests.post", return_value=_created([_PARTICIPANT_ROW])):
            resp = self._post({"phone": "0901234567"})

        data = resp.json()
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(data["participant"]["user_id"], _CUSTOMER_ID)

    def test_add_by_name_phone_new_customer_upserted(self):
        """Customer not found → upsert → insert participant → 201."""
        new_customer = dict(_CUSTOMER_ROW)

        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([_SLOT_ROW_NO_MAX]),
                 _ok([_COURT_ROW]),
                 _empty(),             # search by phone → not found
                 _empty(),             # no duplicate
             ]), \
             patch("courts.views.requests.post", side_effect=[
                 _created([new_customer]),   # upsert customer
                 _created([_PARTICIPANT_ROW]),  # insert participant
             ]):
            resp = self._post({"name": "Nguyen Van A", "phone": "0901234567"})

        data = resp.json()
        self.assertEqual(resp.status_code, 201)
        self.assertIn("participant", data)

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_slot_not_found_returns_404(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", return_value=_ok([])):
            resp = self._post({"user_id": _CUSTOMER_ID})
        self.assertEqual(resp.status_code, 404)

    def test_not_court_owner_returns_403(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([_SLOT_ROW]),
                 _ok([{"id": _COURT_ID, "owner_id": _OTHER_OWNER_ID}]),
             ]):
            resp = self._post({"user_id": _CUSTOMER_ID})
        self.assertEqual(resp.status_code, 403)

    def test_customer_not_found_returns_404(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([_SLOT_ROW]),
                 _ok([_COURT_ROW]),
                 _ok([]),            # customer lookup → not found
             ]):
            resp = self._post({"user_id": _CUSTOMER_ID})
        self.assertEqual(resp.status_code, 404)

    def test_already_participant_returns_409(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get", side_effect=[
                 _ok([_SLOT_ROW]),
                 _ok([_COURT_ROW]),
                 _ok([_CUSTOMER_ROW]),
                 _ok([{"id": _PARTICIPANT_ID}]),  # duplicate found
             ]):
            resp = self._post({"user_id": _CUSTOMER_ID})
        self.assertEqual(resp.status_code, 409)

    def test_slot_service_unavailable_returns_503(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD), \
             patch("courts.views.requests.get",
                   side_effect=_requests_lib.exceptions.RequestException("network")):
            resp = self._post({"user_id": _CUSTOMER_ID})
        self.assertEqual(resp.status_code, 503)
