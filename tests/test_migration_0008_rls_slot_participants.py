"""Tests for migration 0008 — RLS policies for the slot_participants table.

Covers grava-ea77.2.8:
  - SELECT: authenticated users can read slot_participants rows where they are
    the slot owner (court owner via slots -> courts -> owner_id = auth.uid())
    OR a participant in that slot (user_id = auth.uid()).
  - INSERT: only the slot owner (court owner via slots -> courts) may add
    participants.
  - Non-owners and non-participants cannot read or insert rows.
  - downgrade() drops all policies and disables RLS (idempotent).

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
MIGRATION_PATH = VERSIONS_DIR / "0008_rls_slot_participants.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0008", MIGRATION_PATH)
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
        assert mod.revision == "0008"

    def test_down_revision(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0008 must chain to a prior migration"
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
    def test_rls_enabled_on_slot_participants(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "ENABLE ROW LEVEL SECURITY" in s and "slot_participants" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER TABLE slot_participants ENABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — SELECT policy (slot owner OR participant)
# ---------------------------------------------------------------------------

class TestUpgradeSelectPolicy:
    def test_select_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "slot_participants" in s
            and "SELECT" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE a SELECT policy on slot_participants; "
            f"got: {sql_stmts}"
        )

    def test_select_policy_allows_participant(self):
        """SELECT USING clause must allow the row's user_id = auth.uid() (participant check)."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slot_participants" in s
        ]
        assert select_stmts, "No SELECT CREATE POLICY statement found for slot_participants"
        combined = " ".join(select_stmts)
        assert "user_id" in combined and "auth.uid()" in combined, (
            "SELECT policy USING clause must reference user_id = auth.uid() "
            f"(participant check); policy SQL: {combined}"
        )

    def test_select_policy_allows_slot_owner(self):
        """SELECT USING clause must allow the slot owner (court owner via slots -> courts)."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slot_participants" in s
        ]
        assert select_stmts, "No SELECT CREATE POLICY statement found for slot_participants"
        combined = " ".join(select_stmts)
        # Must join slots -> courts to check owner_id
        assert "slots" in combined and "courts" in combined and "owner_id" in combined, (
            "SELECT policy USING clause must include a slot-owner sub-query "
            "(e.g. slot_id IN (SELECT s.id FROM slots s JOIN courts c ON c.id = s.court_id "
            "WHERE c.owner_id = auth.uid())); "
            f"policy SQL: {combined}"
        )

    def test_select_policy_targets_authenticated(self):
        """SELECT policy must target the authenticated role."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slot_participants" in s
        ]
        combined = " ".join(select_stmts)
        assert "authenticated" in combined, (
            "SELECT policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# upgrade() — INSERT policy (slot owner only)
# ---------------------------------------------------------------------------

class TestUpgradeInsertPolicy:
    def test_insert_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "INSERT" in s
            and "slot_participants" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE an INSERT policy on slot_participants; "
            f"got: {sql_stmts}"
        )

    def test_insert_policy_targets_authenticated(self):
        """INSERT policy must target the authenticated role."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "slot_participants" in s
        ]
        assert insert_stmts, "No INSERT CREATE POLICY statement found for slot_participants"
        combined = " ".join(insert_stmts)
        assert "authenticated" in combined, (
            "INSERT policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_restricts_to_slot_owner(self):
        """INSERT policy must restrict to slot owner (court owner via slots -> courts)."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "slot_participants" in s
        ]
        assert insert_stmts, "No INSERT CREATE POLICY statement found for slot_participants"
        combined = " ".join(insert_stmts)
        # Must check slot owner via slots -> courts
        assert "slots" in combined and "courts" in combined and "owner_id" in combined, (
            "INSERT policy must restrict to slot owners via a slots -> courts sub-query "
            "(e.g. WITH CHECK slot_id IN (SELECT s.id FROM slots s JOIN courts c ON c.id = s.court_id "
            "WHERE c.owner_id = auth.uid())); "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_uses_with_check(self):
        """INSERT policy must use WITH CHECK to validate new rows."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "slot_participants" in s
        ]
        assert insert_stmts, "No INSERT CREATE POLICY statement found for slot_participants"
        combined = " ".join(insert_stmts)
        assert "WITH CHECK" in combined, (
            "INSERT policy must use WITH CHECK to validate new rows; "
            f"policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# downgrade() — policies dropped, RLS disabled
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_rls_disabled_on_slot_participants(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DISABLE ROW LEVEL SECURITY" in s and "slot_participants" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER TABLE slot_participants DISABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )

    def test_policies_dropped(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DROP POLICY" in s and "slot_participants" in s
            for s in sql_stmts
        ), (
            "downgrade() must DROP policies on slot_participants; "
            f"got: {sql_stmts}"
        )

    def test_all_drop_policies_use_if_exists(self):
        """DROP POLICY must be idempotent — use IF EXISTS."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "slot_participants" in s
        ]
        for stmt in drop_stmts:
            assert "IF EXISTS" in stmt, (
                f"DROP POLICY must use IF EXISTS for idempotency; got: {stmt!r}"
            )

    def test_both_policies_dropped(self):
        """downgrade() must drop at least 2 policies (SELECT, INSERT)."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "slot_participants" in s
        ]
        assert len(drop_stmts) >= 2, (
            f"downgrade() must drop at least 2 policies (SELECT, INSERT); "
            f"found {len(drop_stmts)} DROP POLICY statements: {drop_stmts}"
        )
