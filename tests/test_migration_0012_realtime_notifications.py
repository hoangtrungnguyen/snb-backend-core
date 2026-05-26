"""Tests for migration 0012 — Supabase Realtime on `notifications` table.

Covers grava-8038.3 (BCORE-042):
  - Enables REPLICA IDENTITY FULL on `notifications` so that UPDATE events
    carry the full old and new row (required for Realtime change-data-capture,
    e.g. when read_at is stamped on notification-centre open).
  - Adds the `notifications` table to the `supabase_realtime` publication so
    Flutter clients receive live INSERT events when a new notification row is
    written by the backend (or a Postgres trigger).
  - The existing RLS SELECT policy `notifications_select_owner` (from migration
    0005) already ensures each user only sees their own notifications; no new
    RLS policy is needed — Realtime re-uses the table's existing policies.
  - downgrade() removes the publication member and resets REPLICA IDENTITY
    back to DEFAULT — idempotent.

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
MIGRATION_PATH = VERSIONS_DIR / "0012_realtime_notifications.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0012", MIGRATION_PATH)
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
        assert mod.revision == "0012"

    def test_down_revision_not_none(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0012 must chain to a prior migration"
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
    def test_replica_identity_full_set_on_notifications(self):
        """upgrade() must set REPLICA IDENTITY FULL on notifications.

        Supabase Realtime broadcasts change events that include both the old
        and the new row values.  By default PostgreSQL only includes the PK in
        the WAL for UPDATE/DELETE events (REPLICA IDENTITY DEFAULT).  Setting
        FULL ensures the entire old row is captured so clients can reconcile
        local state (e.g. detecting that read_at changed from NULL to a
        timestamp).
        """
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "REPLICA IDENTITY FULL" in s and "notifications" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER TABLE notifications REPLICA IDENTITY FULL; "
            f"got: {sql_stmts}"
        )

    def test_replica_identity_set_before_publication(self):
        """REPLICA IDENTITY FULL must be set before adding to publication."""
        sql_stmts = _collect_upgrade_sql()
        replica_idx = next(
            (i for i, s in enumerate(sql_stmts) if "REPLICA IDENTITY FULL" in s and "notifications" in s),
            None,
        )
        publication_idx = next(
            (i for i, s in enumerate(sql_stmts) if "supabase_realtime" in s and "ADD TABLE" in s),
            None,
        )
        assert replica_idx is not None, "REPLICA IDENTITY FULL statement not found"
        assert publication_idx is not None, "ADD TABLE to supabase_realtime not found"
        assert replica_idx < publication_idx, (
            "REPLICA IDENTITY FULL must be set before adding to the publication"
        )


# ---------------------------------------------------------------------------
# upgrade() — Supabase Realtime publication
# ---------------------------------------------------------------------------

class TestUpgradeRealtimePublication:
    def test_notifications_added_to_supabase_realtime_publication(self):
        """upgrade() must add `notifications` to the supabase_realtime publication."""
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "supabase_realtime" in s and "notifications" in s and "ADD TABLE" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER PUBLICATION supabase_realtime ADD TABLE notifications; "
            f"got: {sql_stmts}"
        )

    def test_add_table_statement_is_present(self):
        """The publication ADD TABLE statement must use the correct syntax."""
        sql_stmts = _collect_upgrade_sql()
        matching = [
            s for s in sql_stmts
            if "supabase_realtime" in s and "notifications" in s and "ADD TABLE" in s
        ]
        assert len(matching) >= 1, (
            "Expected at least one ADD TABLE supabase_realtime ... notifications statement"
        )


# ---------------------------------------------------------------------------
# upgrade() — No new RLS policy needed (reuse migration 0005 policies)
# ---------------------------------------------------------------------------

class TestUpgradeNoNewRLSPolicies:
    def test_no_new_create_policy_statements(self):
        """upgrade() must not create new RLS policies.

        Migration 0005 already created SELECT and UPDATE policies on the
        notifications table (notifications_select_owner,
        notifications_update_owner).  Supabase Realtime re-uses these
        existing policies to decide which INSERT/UPDATE events to broadcast
        to each subscriber — no additional policy is required.
        """
        sql_stmts = _collect_upgrade_sql()
        create_policy_stmts = [s for s in sql_stmts if "CREATE POLICY" in s]
        assert len(create_policy_stmts) == 0, (
            "upgrade() must not create new RLS policies — migration 0005 "
            "already covers RLS for notifications; "
            f"unexpected CREATE POLICY statements: {create_policy_stmts}"
        )


# ---------------------------------------------------------------------------
# downgrade() — publication member removed, REPLICA IDENTITY reset
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_notifications_removed_from_supabase_realtime_publication(self):
        """downgrade() must remove notifications from the supabase_realtime publication."""
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "supabase_realtime" in s and "notifications" in s and "DROP TABLE" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER PUBLICATION supabase_realtime DROP TABLE notifications; "
            f"got: {sql_stmts}"
        )

    def test_replica_identity_reset_to_default(self):
        """downgrade() must reset notifications REPLICA IDENTITY back to DEFAULT."""
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "REPLICA IDENTITY DEFAULT" in s and "notifications" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER TABLE notifications REPLICA IDENTITY DEFAULT; "
            f"got: {sql_stmts}"
        )

    def test_publication_removed_before_replica_identity_reset(self):
        """Publication drop should precede REPLICA IDENTITY reset in downgrade."""
        sql_stmts = _collect_downgrade_sql()
        drop_pub_idx = next(
            (i for i, s in enumerate(sql_stmts) if "supabase_realtime" in s and "DROP TABLE" in s),
            None,
        )
        reset_idx = next(
            (i for i, s in enumerate(sql_stmts) if "REPLICA IDENTITY DEFAULT" in s),
            None,
        )
        assert drop_pub_idx is not None, "DROP TABLE from supabase_realtime not found"
        assert reset_idx is not None, "REPLICA IDENTITY DEFAULT reset not found"
        assert drop_pub_idx < reset_idx, (
            "Publication DROP TABLE should come before REPLICA IDENTITY DEFAULT reset"
        )

    def test_no_drop_policy_statements(self):
        """downgrade() must not drop policies (they belong to migration 0005)."""
        sql_stmts = _collect_downgrade_sql()
        drop_policy_stmts = [s for s in sql_stmts if "DROP POLICY" in s]
        assert len(drop_policy_stmts) == 0, (
            "downgrade() must not drop RLS policies — those are managed by "
            "migration 0005; "
            f"unexpected DROP POLICY statements: {drop_policy_stmts}"
        )
