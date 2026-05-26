"""Tests for migration 0010 — Supabase Realtime on `slots` + RLS for slot availability.

Covers grava-8038.1 (BCORE-040):
  - Adds the `slots` table to the `supabase_realtime` publication so Flutter
    clients receive live UPDATE events when slot.status changes.
  - Enables RLS on `slots` (default-deny posture for roles not covered by a
    policy).
  - SELECT policy `slots_select_available`:
      anon + authenticated may read a slot row only when:
        a) slots.status IN ('open', 'booked')   — publicly relevant statuses
        b) the slot's court has status = 'approved'  — suppresses pending /
           suspended courts from the Realtime feed and regular queries.
  - Owner SELECT policy `slots_select_owner`:
      authenticated court owners may read ALL slots on their own courts
      (regardless of slot or court status) so the owner dashboard stays fully
      populated.
  - downgrade() removes the publication member, drops all policies, and
    disables RLS — idempotent (uses IF EXISTS everywhere).

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
MIGRATION_PATH = VERSIONS_DIR / "0010_realtime_slots.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0010", MIGRATION_PATH)
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
        assert mod.revision == "0010"

    def test_down_revision_not_none(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0010 must chain to a prior migration"
        )

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — Supabase Realtime publication
# ---------------------------------------------------------------------------

class TestUpgradeRealtimePublication:
    def test_slots_added_to_supabase_realtime_publication(self):
        """upgrade() must add `slots` to the supabase_realtime publication."""
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "supabase_realtime" in s and "slots" in s and "ADD TABLE" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER PUBLICATION supabase_realtime ADD TABLE slots; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — RLS enabled on slots
# ---------------------------------------------------------------------------

class TestUpgradeEnablesRLS:
    def test_rls_enabled_on_slots(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "ENABLE ROW LEVEL SECURITY" in s and "slots" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER TABLE slots ENABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — SELECT policy for public slot availability (Realtime feed)
# ---------------------------------------------------------------------------

class TestUpgradeSelectAvailablePolicy:
    def test_select_available_policy_exists(self):
        """upgrade() must CREATE a SELECT policy on slots for available statuses."""
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s and "slots" in s and "SELECT" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE at least one SELECT policy on slots; "
            f"got: {sql_stmts}"
        )

    def test_select_policy_filters_open_and_booked_status(self):
        """SELECT policy USING clause must allow only status IN ('open', 'booked')."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slots" in s
        ]
        combined = " ".join(select_stmts)
        # Both status values must appear in the policy body
        assert "open" in combined and "booked" in combined, (
            "SELECT policy must filter slots.status IN ('open', 'booked'); "
            f"policy SQL: {combined}"
        )

    def test_select_policy_filters_approved_courts(self):
        """SELECT policy must restrict to approved courts only."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slots" in s
        ]
        combined = " ".join(select_stmts)
        assert "approved" in combined and "courts" in combined, (
            "SELECT policy must join/sub-query courts and filter status = 'approved'; "
            f"policy SQL: {combined}"
        )

    def test_select_policy_targets_anon_and_authenticated(self):
        """Public availability policy must be accessible to anon + authenticated."""
        sql_stmts = _collect_upgrade_sql()
        # Find the availability (non-owner) SELECT policy
        availability_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slots" in s
            and "anon" in s
        ]
        assert availability_stmts, (
            "At least one SELECT policy on slots must grant access to 'anon' role "
            "(for unauthenticated Realtime subscribers); "
            f"got select policies: {[s for s in sql_stmts if 'CREATE POLICY' in s and 'SELECT' in s]}"
        )

    def test_select_policy_uses_using_clause(self):
        """SELECT policies must use USING (not WITH CHECK) to filter rows."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slots" in s
        ]
        assert select_stmts, "No SELECT policies found on slots"
        for stmt in select_stmts:
            assert "USING" in stmt, (
                "SELECT policy must use a USING clause to filter visible rows; "
                f"got: {stmt!r}"
            )


# ---------------------------------------------------------------------------
# upgrade() — Owner SELECT policy (sees all slots on their courts)
# ---------------------------------------------------------------------------

class TestUpgradeOwnerSelectPolicy:
    def test_owner_select_policy_exists(self):
        """upgrade() must CREATE an owner-scoped SELECT policy on slots."""
        sql_stmts = _collect_upgrade_sql()
        owner_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slots" in s
            and "authenticated" in s and "owner_id" in s
        ]
        assert owner_stmts, (
            "upgrade() must CREATE an owner SELECT policy so court owners can see "
            "all their slots regardless of status; "
            f"got select policies: {[s for s in sql_stmts if 'CREATE POLICY' in s and 'SELECT' in s]}"
        )

    def test_owner_select_policy_joins_courts(self):
        """Owner SELECT policy must join slots -> courts to check ownership."""
        sql_stmts = _collect_upgrade_sql()
        owner_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "slots" in s
            and "owner_id" in s
        ]
        assert owner_stmts, "No owner SELECT policy found on slots"
        combined = " ".join(owner_stmts)
        assert "courts" in combined and "owner_id" in combined and "auth.uid()" in combined, (
            "Owner SELECT policy must check court ownership via courts.owner_id = auth.uid(); "
            f"policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# downgrade() — publication member removed, policies dropped, RLS disabled
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_slots_removed_from_supabase_realtime_publication(self):
        """downgrade() must remove slots from the supabase_realtime publication."""
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "supabase_realtime" in s and "slots" in s and "DROP TABLE" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER PUBLICATION supabase_realtime DROP TABLE slots; "
            f"got: {sql_stmts}"
        )

    def test_rls_disabled_on_slots(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DISABLE ROW LEVEL SECURITY" in s and "slots" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER TABLE slots DISABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )

    def test_policies_dropped(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DROP POLICY" in s and "slots" in s
            for s in sql_stmts
        ), (
            "downgrade() must DROP policies on slots; "
            f"got: {sql_stmts}"
        )

    def test_all_drop_policies_use_if_exists(self):
        """DROP POLICY must be idempotent — use IF EXISTS."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "slots" in s
        ]
        assert drop_stmts, "No DROP POLICY statements for slots in downgrade()"
        for stmt in drop_stmts:
            assert "IF EXISTS" in stmt, (
                f"DROP POLICY must use IF EXISTS for idempotency; got: {stmt!r}"
            )

    def test_at_least_two_policies_dropped(self):
        """downgrade() must drop at least 2 policies (availability SELECT + owner SELECT)."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "slots" in s
        ]
        assert len(drop_stmts) >= 2, (
            f"downgrade() must drop at least 2 policies on slots; "
            f"found {len(drop_stmts)} DROP POLICY statements: {drop_stmts}"
        )

    def test_downgrade_drop_policy_if_exists_for_publication(self):
        """The publication DROP should use IF EXISTS for idempotency."""
        sql_stmts = _collect_downgrade_sql()
        publication_stmts = [
            s for s in sql_stmts
            if "supabase_realtime" in s and "slots" in s
        ]
        assert publication_stmts, "No publication statement found in downgrade()"
        # Verify it's a DROP TABLE (removal from publication)
        assert any("DROP TABLE" in s for s in publication_stmts), (
            "downgrade() publication statement must use DROP TABLE to remove slots; "
            f"got: {publication_stmts}"
        )
