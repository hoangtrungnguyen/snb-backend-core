"""Tests for migration 0011 — Supabase Realtime on `bookings` + RLS update.

Covers grava-8038.2 (BCORE-041):
  - Enables REPLICA IDENTITY FULL on `bookings` so that UPDATE events carry
    the full old and new row (required for Realtime change-data-capture).
  - Adds the `bookings` table to the `supabase_realtime` publication so Flutter
    clients receive live UPDATE events when booking.status changes.
  - SELECT policy `bookings_select_player` (for Realtime):
      authenticated users may read a booking if they are the booking owner
      (user_id = auth.uid()) OR the court owner
      (court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())).
      This mirrors the existing 0004 policy and ensures the Realtime channel
      only delivers rows the subscriber is allowed to see.
  - downgrade() removes the publication member, sets REPLICA IDENTITY back
    to DEFAULT, drops the new policy, and disables the Realtime-scoped policy
    — all idempotent (uses IF EXISTS where applicable).

All tests intercept op.execute() to verify SQL fragments — no live DB required.
"""

from __future__ import annotations

import importlib
import importlib.util
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0011_realtime_bookings.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0011", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_upgrade_sql() -> list[str]:
    """Run upgrade() with a patched op.execute and return all SQL strings."""
    mod = _load_migration()
    executed: list[str] = []

    import alembic.op as _op
    original_execute = _op.execute

    def fake_execute(sql: str) -> None:
        executed.append(str(sql))

    _op.execute = fake_execute
    try:
        mod.upgrade()
    finally:
        _op.execute = original_execute

    return executed


def _collect_downgrade_sql() -> list[str]:
    """Run downgrade() with a patched op.execute and return all SQL strings."""
    mod = _load_migration()
    executed: list[str] = []

    import alembic.op as _op
    original_execute = _op.execute

    def fake_execute(sql: str) -> None:
        executed.append(str(sql))

    _op.execute = fake_execute
    try:
        mod.downgrade()
    finally:
        _op.execute = original_execute

    return executed


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------

class TestMigrationModuleStructure:
    def test_migration_file_exists(self):
        assert MIGRATION_PATH.exists(), (
            f"Migration file not found at {MIGRATION_PATH}"
        )

    def test_revision_id(self):
        mod = _load_migration()
        assert mod.revision == "0011"

    def test_down_revision_not_none(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0011 must chain to a prior migration"
        )

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — REPLICA IDENTITY FULL
# ---------------------------------------------------------------------------

class TestUpgradeReplicaIdentity:
    def test_replica_identity_full_set_on_bookings(self):
        """upgrade() must set REPLICA IDENTITY FULL on bookings for Realtime UPDATEs."""
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "REPLICA IDENTITY FULL" in s and "bookings" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER TABLE bookings REPLICA IDENTITY FULL; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — Supabase Realtime publication
# ---------------------------------------------------------------------------

class TestUpgradeRealtimePublication:
    def test_bookings_added_to_supabase_realtime_publication(self):
        """upgrade() must add `bookings` to the supabase_realtime publication."""
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "supabase_realtime" in s and "bookings" in s and "ADD TABLE" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER PUBLICATION supabase_realtime ADD TABLE bookings; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — SELECT policy for Realtime (booking owner OR court owner)
# ---------------------------------------------------------------------------

class TestUpgradeSelectPolicy:
    def test_select_policy_created(self):
        """upgrade() must CREATE a SELECT policy on bookings for Realtime."""
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s and "bookings" in s and "SELECT" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE a SELECT policy on bookings; "
            f"got: {sql_stmts}"
        )

    def test_select_policy_allows_booking_owner(self):
        """SELECT USING clause must include booking owner check (user_id = auth.uid())."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "bookings" in s
        ]
        assert select_stmts, "No SELECT CREATE POLICY statement found for bookings"
        combined = " ".join(select_stmts)
        assert "user_id" in combined and "auth.uid()" in combined, (
            "SELECT policy USING clause must reference user_id = auth.uid() "
            f"(booking owner check); policy SQL: {combined}"
        )

    def test_select_policy_allows_court_owner(self):
        """SELECT USING clause must include a court owner sub-query."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "bookings" in s
        ]
        assert select_stmts, "No SELECT CREATE POLICY statement found for bookings"
        combined = " ".join(select_stmts)
        assert "courts" in combined.lower() and "owner_id" in combined, (
            "SELECT policy USING clause must include a court-owner sub-query "
            "(e.g. court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())); "
            f"policy SQL: {combined}"
        )

    def test_select_policy_targets_authenticated(self):
        """SELECT policy must target the authenticated role."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "bookings" in s
        ]
        combined = " ".join(select_stmts)
        assert "authenticated" in combined, (
            "SELECT policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )

    def test_select_policy_uses_using_clause(self):
        """SELECT policy must use USING (not WITH CHECK) to filter rows."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "bookings" in s
        ]
        assert select_stmts, "No SELECT policies found on bookings"
        for stmt in select_stmts:
            assert "USING" in stmt, (
                "SELECT policy must use a USING clause to filter visible rows; "
                f"got: {stmt!r}"
            )


# ---------------------------------------------------------------------------
# downgrade() — publication member removed, REPLICA IDENTITY reset, policy dropped
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_bookings_removed_from_supabase_realtime_publication(self):
        """downgrade() must remove bookings from the supabase_realtime publication."""
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "supabase_realtime" in s and "bookings" in s and "DROP TABLE" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER PUBLICATION supabase_realtime DROP TABLE bookings; "
            f"got: {sql_stmts}"
        )

    def test_replica_identity_reset_to_default(self):
        """downgrade() must reset bookings REPLICA IDENTITY back to DEFAULT."""
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "REPLICA IDENTITY DEFAULT" in s and "bookings" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER TABLE bookings REPLICA IDENTITY DEFAULT; "
            f"got: {sql_stmts}"
        )

    def test_select_policy_dropped(self):
        """downgrade() must DROP the SELECT policy added by this migration."""
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DROP POLICY" in s and "bookings" in s
            for s in sql_stmts
        ), (
            "downgrade() must DROP the SELECT policy on bookings; "
            f"got: {sql_stmts}"
        )

    def test_drop_policy_uses_if_exists(self):
        """DROP POLICY must be idempotent — use IF EXISTS."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "bookings" in s
        ]
        assert drop_stmts, "No DROP POLICY statements for bookings in downgrade()"
        for stmt in drop_stmts:
            assert "IF EXISTS" in stmt, (
                f"DROP POLICY must use IF EXISTS for idempotency; got: {stmt!r}"
            )
