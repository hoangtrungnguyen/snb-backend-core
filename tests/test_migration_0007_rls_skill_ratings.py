"""Tests for migration 0007 — RLS policies for the skill_ratings table.

Covers grava-ea77.2.7:
  - INSERT: rated_by must be a court owner (auth.uid() = owner_id in courts) AND
    the player (player_id) must have visited one of that court owner's courts
    (player_id IN (SELECT user_id FROM bookings WHERE court_id IN
                   (SELECT id FROM courts WHERE owner_id = auth.uid()))).
  - UPDATE: same constraint as INSERT — both USING and WITH CHECK enforce
    the court-owner + visited-player relationship.
  - SELECT: authenticated users can read all skill_ratings (open read access).
  - Non-qualifying users cannot insert or update skill_ratings (RLS enforced).

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
MIGRATION_PATH = VERSIONS_DIR / "0007_rls_skill_ratings.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0007", MIGRATION_PATH)
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
        assert mod.revision == "0007"

    def test_down_revision(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0007 must chain to a prior migration"
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
    def test_rls_enabled_on_skill_ratings(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "ENABLE ROW LEVEL SECURITY" in s and "skill_ratings" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER TABLE skill_ratings ENABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — SELECT policy (open read for authenticated users)
# ---------------------------------------------------------------------------

class TestUpgradeSelectPolicy:
    def test_select_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "skill_ratings" in s
            and "SELECT" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE a SELECT policy on skill_ratings; "
            f"got: {sql_stmts}"
        )

    def test_select_policy_targets_authenticated(self):
        """SELECT policy must target the authenticated role."""
        sql_stmts = _collect_upgrade_sql()
        select_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "SELECT" in s and "skill_ratings" in s
        ]
        combined = " ".join(select_stmts)
        assert "authenticated" in combined, (
            "SELECT policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# upgrade() — INSERT policy (court owner who owns a court the player visited)
# ---------------------------------------------------------------------------

class TestUpgradeInsertPolicy:
    def test_insert_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "INSERT" in s
            and "skill_ratings" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE an INSERT policy on skill_ratings; "
            f"got: {sql_stmts}"
        )

    def test_insert_policy_targets_authenticated(self):
        """INSERT policy must be scoped to authenticated users."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "skill_ratings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "authenticated" in combined, (
            "INSERT policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_uses_with_check(self):
        """INSERT policy must use WITH CHECK to enforce the constraint."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "skill_ratings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "WITH CHECK" in combined, (
            "INSERT policy must use WITH CHECK to enforce insert constraints; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_checks_court_owner(self):
        """INSERT WITH CHECK must verify rated_by is a court owner (auth.uid())."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "skill_ratings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "auth.uid()" in combined and "owner_id" in combined, (
            "INSERT policy WITH CHECK must verify rated_by = auth.uid() is a court owner; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_checks_player_visited_court(self):
        """INSERT WITH CHECK must verify player_id has visited a court owned by rated_by."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "skill_ratings" in s
        ]
        combined = " ".join(insert_stmts)
        # Must reference both bookings and courts in the sub-query
        assert "bookings" in combined and "courts" in combined, (
            "INSERT policy must check player_id has visited a court owned by auth.uid() "
            "via a sub-query joining bookings and courts; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_checks_player_id(self):
        """INSERT WITH CHECK must reference player_id."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "skill_ratings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "player_id" in combined, (
            "INSERT policy must reference player_id when checking visit history; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_enforces_rated_by_equals_auth_uid(self):
        """INSERT WITH CHECK must enforce rated_by = auth.uid() to prevent impersonation."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "skill_ratings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "rated_by" in combined and "auth.uid()" in combined, (
            "INSERT policy WITH CHECK must enforce rated_by = auth.uid() to prevent "
            "a court owner from inserting with an arbitrary rated_by value; "
            f"policy SQL: {combined}"
        )
        # Verify rated_by = auth.uid() appears as a direct equality check, not
        # only inside a sub-query.
        assert "rated_by = auth.uid()" in combined, (
            "INSERT policy must include 'rated_by = auth.uid()' as a top-level "
            "WITH CHECK condition; "
            f"policy SQL: {combined}"
        )

    def test_insert_policy_booking_status_filter(self):
        """INSERT WITH CHECK bookings sub-query must filter on confirmed/completed status."""
        sql_stmts = _collect_upgrade_sql()
        insert_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "INSERT" in s and "skill_ratings" in s
        ]
        combined = " ".join(insert_stmts)
        assert "status" in combined and (
            "confirmed" in combined or "completed" in combined
        ), (
            "INSERT policy bookings sub-query must filter on booking status "
            "(confirmed/completed) — cancelled or pending bookings must not satisfy "
            "the 'visited' constraint; "
            f"policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# upgrade() — UPDATE policy (court owner who owns a court the player visited)
# ---------------------------------------------------------------------------

class TestUpgradeUpdatePolicy:
    def test_update_policy_exists(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "CREATE POLICY" in s
            and "UPDATE" in s
            and "skill_ratings" in s
            for s in sql_stmts
        ), (
            "upgrade() must CREATE an UPDATE policy on skill_ratings; "
            f"got: {sql_stmts}"
        )

    def test_update_policy_targets_authenticated(self):
        """UPDATE policy must be scoped to authenticated users."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "authenticated" in combined, (
            "UPDATE policy must be scoped to the authenticated role; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_uses_using_clause(self):
        """UPDATE policy must use USING to restrict which rows can be targeted."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "USING" in combined, (
            "UPDATE policy must use USING clause to restrict row access; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_uses_with_check(self):
        """UPDATE policy must use WITH CHECK to enforce the constraint on new values."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "WITH CHECK" in combined, (
            "UPDATE policy must use WITH CHECK to enforce update constraints; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_checks_court_owner(self):
        """UPDATE USING and WITH CHECK must verify rated_by is a court owner."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "auth.uid()" in combined and "owner_id" in combined, (
            "UPDATE policy must verify rated_by = auth.uid() is a court owner; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_checks_player_visited_court(self):
        """UPDATE policy must verify player_id has visited a court owned by rated_by."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "bookings" in combined and "courts" in combined, (
            "UPDATE policy must check player_id has visited a court owned by auth.uid() "
            "via a sub-query joining bookings and courts; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_checks_player_id(self):
        """UPDATE policy must reference player_id."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "player_id" in combined, (
            "UPDATE policy must reference player_id when checking visit history; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_enforces_rated_by_equals_auth_uid(self):
        """UPDATE USING and WITH CHECK must enforce rated_by = auth.uid()."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "rated_by = auth.uid()" in combined, (
            "UPDATE policy must include 'rated_by = auth.uid()' in both USING and "
            "WITH CHECK to prevent a court owner from updating another rater's row "
            "or reassigning rated_by to a different user; "
            f"policy SQL: {combined}"
        )

    def test_update_policy_booking_status_filter(self):
        """UPDATE policy bookings sub-queries must filter on confirmed/completed status."""
        sql_stmts = _collect_upgrade_sql()
        update_stmts = [
            s for s in sql_stmts
            if "CREATE POLICY" in s and "UPDATE" in s and "skill_ratings" in s
        ]
        combined = " ".join(update_stmts)
        assert "status" in combined and (
            "confirmed" in combined or "completed" in combined
        ), (
            "UPDATE policy bookings sub-queries must filter on booking status "
            "(confirmed/completed) — cancelled or pending bookings must not satisfy "
            "the 'visited' constraint; "
            f"policy SQL: {combined}"
        )


# ---------------------------------------------------------------------------
# downgrade() — policies dropped, RLS disabled
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_rls_disabled_on_skill_ratings(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DISABLE ROW LEVEL SECURITY" in s and "skill_ratings" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER TABLE skill_ratings DISABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )

    def test_policies_dropped(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DROP POLICY" in s and "skill_ratings" in s
            for s in sql_stmts
        ), (
            "downgrade() must DROP policies on skill_ratings; "
            f"got: {sql_stmts}"
        )

    def test_all_drop_policies_use_if_exists(self):
        """DROP POLICY must be idempotent — use IF EXISTS."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "skill_ratings" in s
        ]
        for stmt in drop_stmts:
            assert "IF EXISTS" in stmt, (
                f"DROP POLICY must use IF EXISTS for idempotency; got: {stmt!r}"
            )

    def test_at_least_three_policies_dropped(self):
        """downgrade() must drop at least 3 policies (SELECT, INSERT, UPDATE)."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "skill_ratings" in s
        ]
        assert len(drop_stmts) >= 3, (
            f"downgrade() must drop at least 3 policies (SELECT, INSERT, UPDATE); "
            f"found {len(drop_stmts)} DROP POLICY statements: {drop_stmts}"
        )
