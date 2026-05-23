"""Tests for migration 0005 — RLS policies for the notifications table.

Covers grava-ea77.2.6:
  - SELECT: authenticated users can only read notifications where
    user_id = auth.uid().
  - UPDATE: authenticated users can only update notifications where
    user_id = auth.uid().
  - INSERT and DELETE are not permitted via RLS (default-deny).
  - Non-owners cannot read or modify other users' notifications.

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
MIGRATION_PATH = VERSIONS_DIR / "0005_rls_notifications.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0005", MIGRATION_PATH)
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
        assert mod.revision == "0005"

    def test_down_revision(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0005 must chain to a prior migration"
        )

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — RLS enabled
# ---------------------------------------------------------------------------

class TestUpgradeEnablesRLS:
    def test_rls_enabled_on_notifications(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "ENABLE ROW LEVEL SECURITY" in s and "notifications" in s
            for s in sql_stmts
        ), "upgrade() must run ALTER TABLE notifications ENABLE ROW LEVEL SECURITY"

    def test_rls_is_first_statement(self):
        """RLS must be enabled before any policy is created."""
        sql_stmts = _collect_upgrade_sql()
        enable_idx = next(
            (i for i, s in enumerate(sql_stmts) if "ENABLE ROW LEVEL SECURITY" in s),
            None,
        )
        policy_idx = next(
            (i for i, s in enumerate(sql_stmts) if "CREATE POLICY" in s),
            None,
        )
        assert enable_idx is not None, "ENABLE ROW LEVEL SECURITY statement not found"
        assert policy_idx is not None, "CREATE POLICY statement not found"
        assert enable_idx < policy_idx, (
            "ENABLE ROW LEVEL SECURITY must appear before any CREATE POLICY"
        )


# ---------------------------------------------------------------------------
# upgrade() — SELECT policy
# ---------------------------------------------------------------------------

class TestUpgradeSelectPolicy:
    def test_select_policy_created(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s and "SELECT" in s and "notifications" in s
            for s in sql_stmts
        ), "upgrade() must create a SELECT policy on notifications"

    def test_select_policy_name(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "notifications_select_owner" in s
            for s in sql_stmts
        ), "SELECT policy must be named 'notifications_select_owner'"

    def test_select_policy_for_authenticated(self):
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts if "SELECT" in s and "notifications" in s
        ]
        assert any(
            "authenticated" in s for s in select_stmts
        ), "SELECT policy must target the 'authenticated' role"

    def test_select_policy_uses_user_id_uid(self):
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts if "notifications_select_owner" in s
        ]
        assert select_stmts, "notifications_select_owner policy not found"
        assert any(
            "user_id" in s and "auth.uid()" in s for s in select_stmts
        ), "SELECT policy USING clause must contain 'user_id' and 'auth.uid()'"

    def test_select_policy_using_clause(self):
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [s for s in sql_stmts if "notifications_select_owner" in s]
        assert any("USING" in s for s in select_stmts), (
            "SELECT policy must use a USING clause"
        )

    def test_select_policy_no_insert_or_delete(self):
        """The SELECT policy must not accidentally grant INSERT or DELETE."""
        sql_stmts = _collect_upgrade_sql()
        select_policy_stmts = [
            s for s in sql_stmts if "notifications_select_owner" in s
        ]
        for stmt in select_policy_stmts:
            assert "FOR INSERT" not in stmt, (
                "SELECT policy must not use FOR INSERT"
            )
            assert "FOR DELETE" not in stmt, (
                "SELECT policy must not use FOR DELETE"
            )


# ---------------------------------------------------------------------------
# upgrade() — UPDATE policy
# ---------------------------------------------------------------------------

class TestUpgradeUpdatePolicy:
    def test_update_policy_created(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s and "UPDATE" in s and "notifications" in s
            for s in sql_stmts
        ), "upgrade() must create an UPDATE policy on notifications"

    def test_update_policy_name(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "notifications_update_owner" in s
            for s in sql_stmts
        ), "UPDATE policy must be named 'notifications_update_owner'"

    def test_update_policy_for_authenticated(self):
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts if "notifications_update_owner" in s
        ]
        assert update_stmts, "notifications_update_owner policy not found"
        assert any(
            "authenticated" in s for s in update_stmts
        ), "UPDATE policy must target the 'authenticated' role"

    def test_update_policy_uses_user_id_uid(self):
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts if "notifications_update_owner" in s
        ]
        assert update_stmts, "notifications_update_owner policy not found"
        assert any(
            "user_id" in s and "auth.uid()" in s for s in update_stmts
        ), "UPDATE policy USING clause must contain 'user_id' and 'auth.uid()'"

    def test_update_policy_using_clause(self):
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [s for s in sql_stmts if "notifications_update_owner" in s]
        assert any("USING" in s for s in update_stmts), (
            "UPDATE policy must use a USING clause"
        )

    def test_update_policy_no_select_or_insert_or_delete(self):
        """The UPDATE policy must not accidentally grant SELECT, INSERT, or DELETE."""
        sql_stmts = _collect_upgrade_sql()
        update_policy_stmts = [
            s for s in sql_stmts if "notifications_update_owner" in s
        ]
        for stmt in update_policy_stmts:
            assert "FOR SELECT" not in stmt, (
                "UPDATE policy must not use FOR SELECT"
            )
            assert "FOR INSERT" not in stmt, (
                "UPDATE policy must not use FOR INSERT"
            )
            assert "FOR DELETE" not in stmt, (
                "UPDATE policy must not use FOR DELETE"
            )


# ---------------------------------------------------------------------------
# upgrade() — No INSERT or DELETE policies (default-deny)
# ---------------------------------------------------------------------------

class TestUpgradeNoInsertDeletePolicies:
    def test_no_insert_policy_created(self):
        """INSERT must be blocked by default-deny — no explicit INSERT policy."""
        sql_stmts = _collect_upgrade_sql()
        insert_policy_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "FOR INSERT" in s and "notifications" in s
        ]
        assert len(insert_policy_stmts) == 0, (
            "upgrade() must NOT create an INSERT policy on notifications — "
            "INSERT is blocked by default-deny"
        )

    def test_no_delete_policy_created(self):
        """DELETE must be blocked by default-deny — no explicit DELETE policy."""
        sql_stmts = _collect_upgrade_sql()
        delete_policy_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "FOR DELETE" in s and "notifications" in s
        ]
        assert len(delete_policy_stmts) == 0, (
            "upgrade() must NOT create a DELETE policy on notifications — "
            "DELETE is blocked by default-deny"
        )

    def test_exactly_two_policies_created(self):
        """upgrade() must create exactly two policies: SELECT and UPDATE."""
        sql_stmts = _collect_upgrade_sql()
        create_policy_stmts = [s for s in sql_stmts if "CREATE POLICY" in s]
        assert len(create_policy_stmts) == 2, (
            f"Expected exactly 2 CREATE POLICY statements, got {len(create_policy_stmts)}"
        )


# ---------------------------------------------------------------------------
# upgrade() — policy targets notifications table
# ---------------------------------------------------------------------------

class TestUpgradePolicyTable:
    def test_select_policy_on_notifications_table(self):
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [s for s in sql_stmts if "notifications_select_owner" in s]
        assert any("ON notifications" in s for s in select_stmts), (
            "SELECT policy must specify ON notifications"
        )

    def test_update_policy_on_notifications_table(self):
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [s for s in sql_stmts if "notifications_update_owner" in s]
        assert any("ON notifications" in s for s in update_stmts), (
            "UPDATE policy must specify ON notifications"
        )


# ---------------------------------------------------------------------------
# downgrade() — policies dropped and RLS disabled
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_select_policy_dropped(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DROP POLICY" in s and "notifications_select_owner" in s
            for s in sql_stmts
        ), "downgrade() must DROP POLICY notifications_select_owner"

    def test_update_policy_dropped(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DROP POLICY" in s and "notifications_update_owner" in s
            for s in sql_stmts
        ), "downgrade() must DROP POLICY notifications_update_owner"

    def test_drop_policy_uses_if_exists(self):
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [s for s in sql_stmts if "DROP POLICY" in s]
        assert all("IF EXISTS" in s for s in drop_stmts), (
            "all DROP POLICY statements must use IF EXISTS"
        )

    def test_rls_disabled_on_notifications(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DISABLE ROW LEVEL SECURITY" in s and "notifications" in s
            for s in sql_stmts
        ), "downgrade() must run ALTER TABLE notifications DISABLE ROW LEVEL SECURITY"

    def test_rls_disabled_after_policies_dropped(self):
        """Policies must be dropped before RLS is disabled."""
        sql_stmts = _collect_downgrade_sql()
        drop_idx = next(
            (i for i, s in enumerate(sql_stmts) if "DROP POLICY" in s),
            None,
        )
        disable_idx = next(
            (i for i, s in enumerate(sql_stmts) if "DISABLE ROW LEVEL SECURITY" in s),
            None,
        )
        assert drop_idx is not None, "DROP POLICY statement not found in downgrade()"
        assert disable_idx is not None, (
            "DISABLE ROW LEVEL SECURITY statement not found in downgrade()"
        )
        assert drop_idx < disable_idx, (
            "DROP POLICY must appear before DISABLE ROW LEVEL SECURITY"
        )

    def test_exactly_two_policies_dropped(self):
        """downgrade() must drop exactly two policies: SELECT and UPDATE."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [s for s in sql_stmts if "DROP POLICY" in s]
        assert len(drop_stmts) == 2, (
            f"Expected exactly 2 DROP POLICY statements, got {len(drop_stmts)}"
        )
