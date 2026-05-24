"""Tests for migration 0002 — enable RLS on all tables.

Verifies:
  - Migration file exists and is importable.
  - revision / down_revision are correct.
  - upgrade() emits ALTER TABLE ... ENABLE ROW LEVEL SECURITY for every table.
  - downgrade() emits ALTER TABLE ... DISABLE ROW LEVEL SECURITY for every table.
  - No extra RLS policies are created (service-role bypass is Supabase-native).

Covers grava-ea77.2.10.
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
MIGRATION_PATH = VERSIONS_DIR / "0002_enable_rls.py"

ALL_TABLES = [
    "users",
    "courts",
    "recurrence_rules",
    "slots",
    "booking_series",
    "bookings",
    "slot_participants",
    "slot_join_requests",
    "notifications",
    "slot_push_log",
    "skill_ratings",
]


def _load_migration() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("migration_0002", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _collect_sql(fn) -> list[str]:
    """Run fn() with op.execute mocked and return all SQL strings emitted."""
    import alembic.op as _op_module

    executed: list[str] = []
    original_execute = getattr(_op_module, "execute", None)

    def fake_execute(sql: str) -> None:
        executed.append(sql)

    _op_module.execute = fake_execute
    try:
        fn()
    finally:
        if original_execute is not None:
            _op_module.execute = original_execute

    return executed


# ---------------------------------------------------------------------------
# Module structure
# ---------------------------------------------------------------------------


class TestMigrationModuleStructure:
    def test_migration_file_exists(self):
        assert MIGRATION_PATH.exists(), f"Migration file not found at {MIGRATION_PATH}"

    def test_revision_id(self):
        mod = _load_migration()
        assert mod.revision == "0002"

    def test_down_revision(self):
        mod = _load_migration()
        assert mod.down_revision == "0001"

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — ENABLE ROW LEVEL SECURITY
# ---------------------------------------------------------------------------


class TestUpgradeEnablesRLS:
    @pytest.mark.parametrize("table", ALL_TABLES)
    def test_rls_enabled_for_table(self, table: str):
        mod = _load_migration()
        sql_stmts = _collect_sql(mod.upgrade)
        # Normalise whitespace for matching
        normalised = [" ".join(s.split()).upper() for s in sql_stmts]
        expected_fragment = f"ALTER TABLE {table.upper()} ENABLE ROW LEVEL SECURITY"
        assert any(expected_fragment in s for s in normalised), (
            f"upgrade() must emit 'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'; "
            f"found SQL (normalised): {normalised}"
        )

    def test_no_create_policy(self):
        """No RLS policies should be created — service-role bypass is Supabase-native."""
        mod = _load_migration()
        sql_stmts = _collect_sql(mod.upgrade)
        policy_stmts = [s for s in sql_stmts if "CREATE POLICY" in s.upper()]
        assert not policy_stmts, (
            f"upgrade() must NOT create RLS policies (service-role bypass is "
            f"Supabase-native); found: {policy_stmts}"
        )


# ---------------------------------------------------------------------------
# downgrade() — DISABLE ROW LEVEL SECURITY
# ---------------------------------------------------------------------------


class TestDowngradeDisablesRLS:
    @pytest.mark.parametrize("table", ALL_TABLES)
    def test_rls_disabled_for_table(self, table: str):
        mod = _load_migration()
        sql_stmts = _collect_sql(mod.downgrade)
        normalised = [" ".join(s.split()).upper() for s in sql_stmts]
        expected_fragment = f"ALTER TABLE {table.upper()} DISABLE ROW LEVEL SECURITY"
        assert any(expected_fragment in s for s in normalised), (
            f"downgrade() must emit 'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY'; "
            f"found SQL (normalised): {normalised}"
        )
