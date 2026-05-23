"""Tests for migration 0006 — RLS policies on booking_series.

These tests verify the migration file structure and the SQL statements
it emits without requiring a live database (offline / mock approach
identical to the pattern used for 0004_rls_bookings.py).

Covers task grava-ea77.2.5:
  - SELECT: series owner OR court owner
  - INSERT: authenticated players (WITH CHECK user_id = auth.uid())
  - UPDATE: series owner OR court owner (USING + WITH CHECK)
"""

from __future__ import annotations

import importlib
import importlib.util
import types
from pathlib import Path
from unittest.mock import call, patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0006_rls_booking_series.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location(
        "migration_0006", MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
        assert mod.revision == "0006"

    def test_down_revision(self):
        mod = _load_migration()
        assert mod.down_revision == "0001"

    def test_has_upgrade(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_has_downgrade(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() SQL assertions
# ---------------------------------------------------------------------------

class TestUpgradeSql:
    def _run_upgrade(self):
        """Run upgrade() with op.execute mocked; return list of SQL strings."""
        mod = _load_migration()
        executed: list[str] = []

        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: executed.append(sql.strip())

        with patch.object(mod, "op", mock_op):
            mod.upgrade()

        return executed

    def test_enable_rls(self):
        sql_calls = self._run_upgrade()
        assert any(
            "ALTER TABLE booking_series ENABLE ROW LEVEL SECURITY" in s
            for s in sql_calls
        ), "upgrade() must enable RLS on booking_series"

    def test_select_policy_exists(self):
        sql_calls = self._run_upgrade()
        combined = " ".join(sql_calls)
        assert "booking_series_select_owner" in combined, (
            "SELECT policy 'booking_series_select_owner' must be created"
        )
        assert "FOR SELECT" in combined

    def test_select_policy_checks_series_owner(self):
        sql_calls = self._run_upgrade()
        combined = " ".join(sql_calls)
        assert "user_id = auth.uid()" in combined, (
            "SELECT policy must include user_id = auth.uid() (series owner)"
        )

    def test_select_policy_checks_court_owner(self):
        sql_calls = self._run_upgrade()
        combined = " ".join(sql_calls)
        assert "SELECT id FROM courts WHERE owner_id = auth.uid()" in combined, (
            "SELECT policy must include court owner sub-select"
        )

    def test_insert_policy_exists(self):
        sql_calls = self._run_upgrade()
        combined = " ".join(sql_calls)
        assert "booking_series_insert_player" in combined, (
            "INSERT policy 'booking_series_insert_player' must be created"
        )
        assert "FOR INSERT" in combined

    def test_insert_policy_with_check(self):
        sql_calls = self._run_upgrade()
        combined = " ".join(sql_calls)
        assert "WITH CHECK" in combined, (
            "INSERT policy must use WITH CHECK"
        )

    def test_update_policy_exists(self):
        sql_calls = self._run_upgrade()
        combined = " ".join(sql_calls)
        assert "booking_series_update_owner" in combined, (
            "UPDATE policy 'booking_series_update_owner' must be created"
        )
        assert "FOR UPDATE" in combined

    def test_update_policy_uses_using_and_with_check(self):
        sql_calls = self._run_upgrade()
        # The UPDATE policy SQL should contain both USING and WITH CHECK
        update_sqls = [s for s in sql_calls if "FOR UPDATE" in s]
        assert update_sqls, "No UPDATE policy SQL found"
        update_sql = update_sqls[0]
        assert "USING" in update_sql, "UPDATE policy must have USING clause"
        assert "WITH CHECK" in update_sql, "UPDATE policy must have WITH CHECK clause"

    def test_all_policies_are_authenticated(self):
        sql_calls = self._run_upgrade()
        combined = " ".join(sql_calls)
        assert combined.count("TO authenticated") >= 3, (
            "All three policies must be restricted to the 'authenticated' role"
        )


# ---------------------------------------------------------------------------
# downgrade() SQL assertions
# ---------------------------------------------------------------------------

class TestDowngradeSql:
    def _run_downgrade(self):
        """Run downgrade() with op.execute mocked; return list of SQL strings."""
        mod = _load_migration()
        executed: list[str] = []

        mock_op = MagicMock()
        mock_op.execute.side_effect = lambda sql: executed.append(sql.strip())

        with patch.object(mod, "op", mock_op):
            mod.downgrade()

        return executed

    def test_drops_update_policy(self):
        sql_calls = self._run_downgrade()
        assert any(
            "DROP POLICY IF EXISTS booking_series_update_owner" in s
            for s in sql_calls
        )

    def test_drops_insert_policy(self):
        sql_calls = self._run_downgrade()
        assert any(
            "DROP POLICY IF EXISTS booking_series_insert_player" in s
            for s in sql_calls
        )

    def test_drops_select_policy(self):
        sql_calls = self._run_downgrade()
        assert any(
            "DROP POLICY IF EXISTS booking_series_select_owner" in s
            for s in sql_calls
        )

    def test_disable_rls(self):
        sql_calls = self._run_downgrade()
        assert any(
            "ALTER TABLE booking_series DISABLE ROW LEVEL SECURITY" in s
            for s in sql_calls
        ), "downgrade() must disable RLS on booking_series"

    def test_drop_before_disable(self):
        """Policies must be dropped before RLS is disabled."""
        sql_calls = self._run_downgrade()
        disable_idx = next(
            (i for i, s in enumerate(sql_calls) if "DISABLE ROW LEVEL SECURITY" in s),
            None,
        )
        drop_idxs = [
            i for i, s in enumerate(sql_calls)
            if "DROP POLICY" in s
        ]
        assert disable_idx is not None, "No DISABLE RLS statement"
        assert drop_idxs, "No DROP POLICY statements"
        assert all(idx < disable_idx for idx in drop_idxs), (
            "DROP POLICY statements must come before DISABLE ROW LEVEL SECURITY"
        )
