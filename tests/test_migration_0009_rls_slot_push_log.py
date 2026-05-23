"""Tests for migration 0009 — RLS policies for the slot_push_log table.

Covers grava-ea77.2.9:
  - RLS is enabled on slot_push_log.
  - No SELECT/INSERT policies are created for authenticated or anon users.
    The service-role bypasses RLS by default in Supabase, so enabling RLS
    (default-deny) is sufficient to lock out regular users while keeping the
    table fully accessible to background jobs running as service-role.
  - downgrade() disables RLS and is idempotent.

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
MIGRATION_PATH = VERSIONS_DIR / "0009_rls_slot_push_log.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION_PATH)
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
        assert mod.revision == "0009"

    def test_down_revision(self):
        """Migration must chain to a prior revision (not None)."""
        mod = _load_migration()
        assert mod.down_revision is not None, (
            "down_revision must not be None — 0009 must chain to a prior migration"
        )

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — RLS enabled (default-deny for all non-service-role connections)
# ---------------------------------------------------------------------------

class TestUpgradeEnablesRLS:
    def test_rls_enabled_on_slot_push_log(self):
        sql_stmts = _collect_upgrade_sql()
        assert any(
            "ENABLE ROW LEVEL SECURITY" in s and "slot_push_log" in s
            for s in sql_stmts
        ), (
            "upgrade() must emit ALTER TABLE slot_push_log ENABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — no policies for anon or authenticated roles
# (service-role bypasses RLS automatically; no explicit policy is needed)
# ---------------------------------------------------------------------------

class TestUpgradeNoPoliciesForRegularUsers:
    def test_no_select_policy_for_authenticated_or_anon(self):
        """No SELECT policy should be created for authenticated or anon roles."""
        sql_stmts = _collect_upgrade_sql()
        for stmt in sql_stmts:
            if "CREATE POLICY" in stmt and "slot_push_log" in stmt and "SELECT" in stmt:
                # A SELECT policy targeted at 'authenticated' or 'anon' must NOT exist.
                assert "authenticated" not in stmt and "anon" not in stmt, (
                    "upgrade() must NOT create a SELECT policy for authenticated/anon "
                    "on slot_push_log — regular users must be blocked by RLS default-deny; "
                    f"found: {stmt!r}"
                )

    def test_no_insert_policy_for_authenticated_or_anon(self):
        """No INSERT policy should be created for authenticated or anon roles."""
        sql_stmts = _collect_upgrade_sql()
        for stmt in sql_stmts:
            if "CREATE POLICY" in stmt and "slot_push_log" in stmt and "INSERT" in stmt:
                assert "authenticated" not in stmt and "anon" not in stmt, (
                    "upgrade() must NOT create an INSERT policy for authenticated/anon "
                    "on slot_push_log — only service-role should be able to insert; "
                    f"found: {stmt!r}"
                )

    def test_no_update_or_delete_policy_for_any_role(self):
        """No UPDATE or DELETE policy should be created on slot_push_log."""
        sql_stmts = _collect_upgrade_sql()
        for stmt in sql_stmts:
            if "CREATE POLICY" in stmt and "slot_push_log" in stmt:
                assert "UPDATE" not in stmt and "DELETE" not in stmt, (
                    "upgrade() must NOT create UPDATE or DELETE policies on slot_push_log; "
                    f"found: {stmt!r}"
                )


# ---------------------------------------------------------------------------
# downgrade() — RLS disabled
# ---------------------------------------------------------------------------

class TestDowngrade:
    def test_rls_disabled_on_slot_push_log(self):
        sql_stmts = _collect_downgrade_sql()
        assert any(
            "DISABLE ROW LEVEL SECURITY" in s and "slot_push_log" in s
            for s in sql_stmts
        ), (
            "downgrade() must emit ALTER TABLE slot_push_log DISABLE ROW LEVEL SECURITY; "
            f"got: {sql_stmts}"
        )

    def test_downgrade_is_idempotent_no_nonexistent_drop(self):
        """downgrade() must not fail if called repeatedly — any DROP POLICY must use IF EXISTS."""
        sql_stmts = _collect_downgrade_sql()
        drop_stmts = [
            s for s in sql_stmts
            if "DROP POLICY" in s and "slot_push_log" in s
        ]
        for stmt in drop_stmts:
            assert "IF EXISTS" in stmt, (
                f"DROP POLICY must use IF EXISTS for idempotency; got: {stmt!r}"
            )
