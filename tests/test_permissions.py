"""
Tests for auth_ext.permissions — IsOwner, IsPlayer, IsCourtOwner, and IsSeriesOwner DRF
permission classes.

Coverage:
- IsOwner: owner role passes, player role fails, unauthenticated fails
- IsPlayer: player role passes, owner role fails, unauthenticated fails
- IsCourtOwner: owner_id matches passes, mismatch fails, court not found → 403,
  network error → 403, no court_id kwarg → 403, unauthenticated → 403
- IsSeriesOwner: player who made booking passes, court owner passes, mismatch fails,
  series not found → 403, network error → 403, no series_id kwarg → 403,
  unauthenticated → 403, non-list Supabase response → 403
- message attribute set on denial
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from auth_ext.middleware import AuthenticatedUser
from auth_ext.permissions import IsCourtOwner, IsOwner, IsPlayer, IsSeriesOwner


def _make_request(*, role: str | None = None, authenticated: bool = True):
    """Build a mock DRF request with the given user state."""
    request = MagicMock()
    if authenticated and role is not None:
        user = AuthenticatedUser(id="uid-123", email="", role=role)
    else:
        # Simulate Django AnonymousUser
        user = MagicMock()
        user.is_authenticated = False
        user.role = None
    request.user = user
    return request


# ---------------------------------------------------------------------------
# IsOwner
# ---------------------------------------------------------------------------


class TestIsOwner:
    def test_owner_role_passes(self):
        """User with role='owner' is granted permission."""
        perm = IsOwner()
        request = _make_request(role="owner")
        assert perm.has_permission(request, None) is True

    def test_player_role_fails(self):
        """User with role='player' is denied by IsOwner."""
        perm = IsOwner()
        request = _make_request(role="player")
        assert perm.has_permission(request, None) is False

    def test_other_role_fails(self):
        """User with an arbitrary non-owner role is denied."""
        perm = IsOwner()
        request = _make_request(role="admin")
        assert perm.has_permission(request, None) is False

    def test_unauthenticated_fails(self):
        """AnonymousUser (is_authenticated=False) is denied."""
        perm = IsOwner()
        request = _make_request(authenticated=False)
        assert perm.has_permission(request, None) is False

    def test_message_attribute_set(self):
        """IsOwner must have a non-empty message attribute."""
        perm = IsOwner()
        assert hasattr(perm, "message")
        assert perm.message  # non-empty string


# ---------------------------------------------------------------------------
# IsPlayer
# ---------------------------------------------------------------------------


class TestIsPlayer:
    def test_player_role_passes(self):
        """User with role='player' is granted permission."""
        perm = IsPlayer()
        request = _make_request(role="player")
        assert perm.has_permission(request, None) is True

    def test_owner_role_fails(self):
        """User with role='owner' is denied by IsPlayer."""
        perm = IsPlayer()
        request = _make_request(role="owner")
        assert perm.has_permission(request, None) is False

    def test_other_role_fails(self):
        """User with an arbitrary non-player role is denied."""
        perm = IsPlayer()
        request = _make_request(role="admin")
        assert perm.has_permission(request, None) is False

    def test_unauthenticated_fails(self):
        """AnonymousUser (is_authenticated=False) is denied by IsPlayer."""
        perm = IsPlayer()
        request = _make_request(authenticated=False)
        assert perm.has_permission(request, None) is False

    def test_message_attribute_set(self):
        """IsPlayer must have a non-empty message attribute."""
        perm = IsPlayer()
        assert hasattr(perm, "message")
        assert perm.message  # non-empty string


# ---------------------------------------------------------------------------
# IsCourtOwner
# ---------------------------------------------------------------------------

_OWNER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_OTHER_ID = "11111111-2222-3333-4444-555555555555"
_COURT_ID = "court-uuid-1234"


def _make_court_request(
    *,
    user_id: str = _OWNER_ID,
    authenticated: bool = True,
    court_id: str | None = _COURT_ID,
):
    """Build a mock DRF request for IsCourtOwner tests."""
    request = MagicMock()
    if authenticated:
        user = AuthenticatedUser(id=user_id, email="", role="owner")
    else:
        user = MagicMock()
        user.is_authenticated = False
        user.id = None
    request.user = user
    # Simulate parser_context kwargs
    if court_id is not None:
        request.parser_context = {"kwargs": {"court_id": court_id}}
    else:
        request.parser_context = {"kwargs": {}}
    return request


def _supabase_response(owner_id: str | None):
    """Return a mock requests.Response for Supabase REST API."""
    resp = MagicMock()
    if owner_id is not None:
        resp.json.return_value = [{"owner_id": owner_id}]
        resp.status_code = 200
    else:
        resp.json.return_value = []
        resp.status_code = 200
    return resp


class TestIsCourtOwner:
    def test_owner_id_matches_passes(self):
        """User whose id matches court.owner_id is granted permission."""
        perm = IsCourtOwner()
        request = _make_court_request(user_id=_OWNER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.return_value = _supabase_response(_OWNER_ID)
            assert perm.has_permission(request, None) is True

    def test_owner_id_mismatch_fails(self):
        """User whose id does NOT match court.owner_id is denied."""
        perm = IsCourtOwner()
        request = _make_court_request(user_id=_OTHER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.return_value = _supabase_response(_OWNER_ID)
            assert perm.has_permission(request, None) is False

    def test_court_not_found_returns_403(self):
        """Empty Supabase response (court not found) → 403, not 404."""
        perm = IsCourtOwner()
        request = _make_court_request(user_id=_OWNER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.return_value = _supabase_response(None)
            assert perm.has_permission(request, None) is False

    def test_network_error_returns_403(self):
        """Network error fetching court → 403, not crash."""
        import requests as req_lib
        perm = IsCourtOwner()
        request = _make_court_request(user_id=_OWNER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.side_effect = req_lib.RequestException("timeout")
            assert perm.has_permission(request, None) is False

    def test_missing_court_id_kwarg_returns_403(self):
        """No court_id in URL kwargs → 403."""
        perm = IsCourtOwner()
        request = _make_court_request(user_id=_OWNER_ID, court_id=None)
        assert perm.has_permission(request, None) is False

    def test_unauthenticated_fails(self):
        """Unauthenticated request → 403 without hitting Supabase."""
        perm = IsCourtOwner()
        request = _make_court_request(authenticated=False)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            assert perm.has_permission(request, None) is False
            mock_get.assert_not_called()

    def test_message_attribute_set(self):
        """IsCourtOwner must have a non-empty message attribute."""
        perm = IsCourtOwner()
        assert hasattr(perm, "message")
        assert perm.message  # non-empty string

    def test_supabase_error_dict_returns_403(self):
        """Supabase returning a JSON error dict (not a list) → 403, not 500.

        When Supabase responds with a valid JSON body that is a dict (e.g. an
        error object like {"code": "PGRST116", "message": "..."}), the old code
        would pass the ``if not data:`` guard (non-empty dict is truthy) and
        then crash on ``data[0]`` (KeyError).  The fixed code guards with
        ``isinstance(data, list)`` and must return False instead of raising.
        """
        perm = IsCourtOwner()
        request = _make_court_request(user_id=_OWNER_ID)
        error_body = {"code": "PGRST116", "message": "Not found", "hint": None}
        with patch("auth_ext.permissions.requests.get") as mock_get:
            resp = MagicMock()
            resp.json.return_value = error_body
            resp.status_code = 406
            mock_get.return_value = resp
            result = perm.has_permission(request, None)
        assert result is False, (
            "Non-list Supabase response must return False (403), not crash with KeyError"
        )

    def test_court_id_url_encoded_in_query(self):
        """court_id is properly used in the Supabase query URL."""
        perm = IsCourtOwner()
        request = _make_court_request(user_id=_OWNER_ID, court_id="court-abc-123")
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.return_value = _supabase_response(_OWNER_ID)
            perm.has_permission(request, None)
            call_url = mock_get.call_args[0][0]
            assert "court-abc-123" in call_url
            assert "owner_id" in call_url


# ---------------------------------------------------------------------------
# IsSeriesOwner
# ---------------------------------------------------------------------------

_PLAYER_ID = "player-uuid-aaaa-bbbb-cccccccccccc"
_COURT_OWNER_ID = "owner-uuid-1111-2222-333333333333"
_THIRD_USER_ID = "third-uuid-9999-8888-777777777777"
_SERIES_ID = "series-uuid-5678"
_SERIES_COURT_ID = "court-uuid-9999"


def _make_series_request(
    *,
    user_id: str = _PLAYER_ID,
    authenticated: bool = True,
    series_id: str | None = _SERIES_ID,
    role: str = "player",
):
    """Build a mock DRF request for IsSeriesOwner tests."""
    request = MagicMock()
    if authenticated:
        user = AuthenticatedUser(id=user_id, email="", role=role)
    else:
        user = MagicMock()
        user.is_authenticated = False
        user.id = None
    request.user = user
    if series_id is not None:
        request.parser_context = {"kwargs": {"series_id": series_id}}
    else:
        request.parser_context = {"kwargs": {}}
    return request


def _series_supabase_response(user_id: str | None, court_id: str | None = _SERIES_COURT_ID):
    """Return a mock Supabase response for booking_series."""
    resp = MagicMock()
    if user_id is not None:
        resp.json.return_value = [{"user_id": user_id, "court_id": court_id}]
        resp.status_code = 200
    else:
        resp.json.return_value = []
        resp.status_code = 200
    return resp


def _court_supabase_response(owner_id: str | None):
    """Return a mock Supabase response for courts."""
    resp = MagicMock()
    if owner_id is not None:
        resp.json.return_value = [{"owner_id": owner_id}]
        resp.status_code = 200
    else:
        resp.json.return_value = []
        resp.status_code = 200
    return resp


class TestIsSeriesOwner:
    def test_series_player_passes(self):
        """User who created the booking series (user_id match) is granted permission."""
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_PLAYER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            # First call: booking_series; second call: courts (not reached since player matches)
            mock_get.return_value = _series_supabase_response(_PLAYER_ID)
            assert perm.has_permission(request, None) is True

    def test_court_owner_passes(self):
        """Court owner (courts.owner_id matches user) is also granted permission."""
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_COURT_OWNER_ID, role="owner")
        with patch("auth_ext.permissions.requests.get") as mock_get:
            # Series shows a different player, but court owner should still pass
            mock_get.side_effect = [
                _series_supabase_response(_PLAYER_ID, _SERIES_COURT_ID),
                _court_supabase_response(_COURT_OWNER_ID),
            ]
            assert perm.has_permission(request, None) is True

    def test_unrelated_user_fails(self):
        """User who is neither the series player nor the court owner is denied."""
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_THIRD_USER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.side_effect = [
                _series_supabase_response(_PLAYER_ID, _SERIES_COURT_ID),
                _court_supabase_response(_COURT_OWNER_ID),
            ]
            assert perm.has_permission(request, None) is False

    def test_series_not_found_returns_403(self):
        """Empty Supabase response for booking_series → 403 (anti-enumeration)."""
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_PLAYER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.return_value = _series_supabase_response(None)
            assert perm.has_permission(request, None) is False

    def test_network_error_series_returns_403(self):
        """Network error fetching booking_series → 403, not crash."""
        import requests as req_lib
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_PLAYER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.side_effect = req_lib.RequestException("timeout")
            assert perm.has_permission(request, None) is False

    def test_network_error_court_returns_403(self):
        """Network error fetching courts (after series found, no user_id match) → 403."""
        import requests as req_lib
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_COURT_OWNER_ID)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.side_effect = [
                _series_supabase_response(_PLAYER_ID, _SERIES_COURT_ID),
                req_lib.RequestException("timeout"),
            ]
            assert perm.has_permission(request, None) is False

    def test_missing_series_id_kwarg_returns_403(self):
        """No series_id in URL kwargs → 403."""
        perm = IsSeriesOwner()
        request = _make_series_request(series_id=None)
        assert perm.has_permission(request, None) is False

    def test_unauthenticated_fails(self):
        """Unauthenticated request → 403 without hitting Supabase."""
        perm = IsSeriesOwner()
        request = _make_series_request(authenticated=False)
        with patch("auth_ext.permissions.requests.get") as mock_get:
            assert perm.has_permission(request, None) is False
            mock_get.assert_not_called()

    def test_supabase_error_dict_series_returns_403(self):
        """Non-list Supabase response for booking_series → 403, not 500."""
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_PLAYER_ID)
        error_body = {"code": "PGRST116", "message": "Not found", "hint": None}
        with patch("auth_ext.permissions.requests.get") as mock_get:
            resp = MagicMock()
            resp.json.return_value = error_body
            resp.status_code = 406
            mock_get.return_value = resp
            assert perm.has_permission(request, None) is False

    def test_supabase_error_dict_court_returns_403(self):
        """Non-list Supabase response for courts → 403, not 500."""
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_COURT_OWNER_ID)
        error_body = {"code": "PGRST116", "message": "Not found", "hint": None}
        with patch("auth_ext.permissions.requests.get") as mock_get:
            court_resp = MagicMock()
            court_resp.json.return_value = error_body
            court_resp.status_code = 406
            mock_get.side_effect = [
                _series_supabase_response(_PLAYER_ID, _SERIES_COURT_ID),
                court_resp,
            ]
            assert perm.has_permission(request, None) is False

    def test_series_id_url_encoded_in_query(self):
        """series_id is URL-encoded in the Supabase booking_series query."""
        perm = IsSeriesOwner()
        request = _make_series_request(user_id=_PLAYER_ID, series_id="series-abc-123")
        with patch("auth_ext.permissions.requests.get") as mock_get:
            mock_get.return_value = _series_supabase_response(_PLAYER_ID)
            perm.has_permission(request, None)
            call_url = mock_get.call_args_list[0][0][0]
            assert "series-abc-123" in call_url
            assert "user_id" in call_url

    def test_message_attribute_set(self):
        """IsSeriesOwner must have a non-empty message attribute."""
        perm = IsSeriesOwner()
        assert hasattr(perm, "message")
        assert perm.message  # non-empty string
