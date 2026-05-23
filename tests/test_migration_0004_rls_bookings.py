"""Tests for migration 0004 — RLS policies for the bookings table.

Covers grava-ea77.2.4:
  - SELECT: authenticated users can read bookings where they are the booking
    owner (user_id = auth.uid()) OR the court owner
    (court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())).
  - INSERT: authenticated players can create new bookings.
  - UPDATE: court owners can update booking status on their courts.
  - Non-owners cannot read, create, or modify other users' bookings.

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
MIGRATION_PATH = VERSIONS_DIR / "0004_rls_bookings.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0004", MIGRATION_PATH)
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
        assert mod.revision == "0004"

    def test_down_revision(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0004 must chain to a prior migration"
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
    def test_rls_enabled_on_bookings(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "ENABLE ROW LEVEL SECURITY" in s and "bookings" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER TABLE bookings ENABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — SELECT policy (booking owner OR court owner)
# ---------------------------------------------------------------------------

class TestUpgradeSelectPolicy:
    def test_select_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "bookings" in s
            and "SELECT" in s
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
        # The court-owner check must join/subquery courts to find owner_id
        assert "courts" in combined.lower() and "owner_id" in combined, (
            "SELECT policy USING clause must include a court-owner sub-query "
            f"(e.g. court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())); "
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


# ---------------------------------------------------------------------------
# upgrade() — INSERT policy (authenticated players)
# ---------------------------------------------------------------------------

class TestUpgradeInsertPolicy:
    def test_insert_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "INSERT" in s
            and "bookings" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE an INSERT policy on bookings; "
            f"got: {sql_stmts}"
        )

    def test_insert_policy_targets_authenticated(self):
        """INSERT policy must allow authenticated users to create bookings."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "bookings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "authenticated" in combined, (
            "INSERT policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_sets_booking_owner(self):
        """INSERT WITH CHECK must ensure the new booking is owned by auth.uid()."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "bookings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "WITH CHECK" in combined and "auth.uid()" in combined, (
            "INSERT policy must use WITH CHECK (user_id = auth.uid()) to prevent "
            "booking on behalf of another user; policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# upgrade() — UPDATE policy (court owner only)
# ---------------------------------------------------------------------------

class TestUpgradeUpdatePolicy:
    def test_update_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "UPDATE" in s
            and "bookings" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE an UPDATE policy on bookings; "
            f"got: {sql_stmts}"
        )

    def test_update_policy_targets_court_owner(self):
        """UPDATE policy USING clause must check that the court belongs to auth.uid()."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "bookings" in s
        ]
        assert update_stmts, "No UPDATE CREATE POLICY statement found for bookings"
        combined = " ".join(update_stmts)
        assert "courts" in combined.lower() and "owner_id" in combined, (
            "UPDATE policy must restrict to court owners via a courts sub-query; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_targets_authenticated(self):
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "bookings" in s
        ]
        combined = " ".join(update_stmts)
        assert "authenticated" in combined, (
            "UPDATE policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# downgrade() — policies dropped, RLS disabled
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_rls_disabled_on_bookings(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DISABLE ROW LEVEL SECURITY" in s and "bookings" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER TABLE bookings DISABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )

    def test_select_policy_dropped(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DROP POLICY" in s and "bookings" in s
            for s in sql_stmts
        ), (
            "downgrade() must DROP policies on bookings; "
            f"got: {sql_stmts}"
        )

    def test_all_policies_use_if_exists(self):
        """DROP POLICY must be idempotent — use IF EXISTS."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [s for s in sql_stmts if "DROP POLICY" in s and "bookings" in s]
        for stmt in drop_stmts:
            assert "IF EXISTS" in stmt, (
                f"DROP POLICY must use IF EXISTS for idempotency; got: {stmt!r}"
            )

    def test_insert_policy_dropped(self):
        sql_stmts = _collect_downgrade_sql()
        # At least one DROP POLICY statement should reference the insert policy name
        drop_stmts = [s for s in sql_stmts if "DROP POLICY" in s and "bookings" in s]
        policy_names = " ".join(drop_stmts)
        # The downgrade should drop at minimum 3 policies (SELECT, INSERT, UPDATE)
        assert len(drop_stmts) >= 3, (
            f"downgrade() must drop at least 3 policies (SELECT, INSERT, UPDATE); "
            f"found {len(drop_stmts)} DROP POLICY statements: {drop_stmts}"
        )
