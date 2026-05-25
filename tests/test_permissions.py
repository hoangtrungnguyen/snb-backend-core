"""
Tests for auth_ext.permissions — IsOwner and IsPlayer DRF permission classes.

Coverage:
- IsOwner: owner role passes, player role fails, unauthenticated fails
- IsPlayer: player role passes, owner role fails, unauthenticated fails
- message attribute set on denial
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from auth_ext.middleware import AuthenticatedUser
from auth_ext.permissions import IsOwner, IsPlayer


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
