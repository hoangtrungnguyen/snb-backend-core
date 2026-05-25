"""Tests for migration 0002 — handle_new_user trigger.

Verifies:
  - Migration file exists with correct revision metadata.
  - upgrade() creates the handle_new_user() PL/pgSQL function.
  - upgrade() creates the AFTER INSERT trigger on auth.users.
  - The trigger function body inserts into public.users with role='player'
    and uses ON CONFLICT DO NOTHING.
  - downgrade() drops the trigger and the function.

No live database is required; all assertions use mock patching of alembic.op.
"""

from __future__ import annotations

import importlib
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0002_handle_new_user_trigger.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location(
        "migration_0002", MIGRATION_PATH
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
        assert mod.revision == "0002"

    def test_down_revision(self):
        """Must chain from 0001."""
        mod = _load_migration()
        assert mod.down_revision == "0001"

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — function creation
# ---------------------------------------------------------------------------

class TestUpgradeCreatesFunction:
    """upgrade() must CREATE OR REPLACE the handle_new_user() function."""

    def _collect_sql(self) -> list[str]:
        mod = _load_migration()
        executed: list[str] = []

        import alembic.op as _op_module
        orig_execute = getattr(_op_module, "execute", None)

        def fake_execute(sql: str) -> None:
            executed.append(sql)

        _op_module.execute = fake_execute
        try:
            mod.upgrade()
        finally:
            if orig_execute is not None:
                _op_module.execute = orig_execute

        return executed

    def test_creates_handle_new_user_function(self):
        sql_stmts = self._collect_sql()
        assert any(
            "handle_new_user" in s and "CREATE" in s for s in sql_stmts
        ), (
            "upgrade() must CREATE the handle_new_user() function; "
            f"found SQL: {sql_stmts}"
        )

    def test_function_inserts_into_public_users(self):
        sql_stmts = self._collect_sql()
        combined = "\n".join(sql_stmts).upper()
        assert "INSERT INTO PUBLIC.USERS" in combined or "INSERT INTO public.users" in "\n".join(sql_stmts), (
            "handle_new_user function must INSERT INTO public.users; "
            f"found SQL: {sql_stmts}"
        )

    def test_function_sets_role_player(self):
        sql_stmts = self._collect_sql()
        combined = "\n".join(sql_stmts)
        assert "player" in combined, (
            "handle_new_user function must set role = 'player'; "
            f"found SQL: {sql_stmts}"
        )

    def test_function_uses_on_conflict_do_nothing(self):
        sql_stmts = self._collect_sql()
        combined = "\n".join(sql_stmts).upper()
        assert "ON CONFLICT DO NOTHING" in combined, (
            "handle_new_user function must use ON CONFLICT DO NOTHING; "
            f"found SQL: {sql_stmts}"
        )


# ---------------------------------------------------------------------------
# upgrade() — trigger creation
# ---------------------------------------------------------------------------

class TestUpgradeCreatesTrigger:
    """upgrade() must CREATE the AFTER INSERT trigger on auth.users."""

    def _collect_sql(self) -> list[str]:
        mod = _load_migration()
        executed: list[str] = []

        import alembic.op as _op_module
        orig_execute = getattr(_op_module, "execute", None)

        def fake_execute(sql: str) -> None:
            executed.append(sql)

        _op_module.execute = fake_execute
        try:
            mod.upgrade()
        finally:
            if orig_execute is not None:
                _op_module.execute = orig_execute

        return executed

    def test_creates_trigger(self):
        sql_stmts = self._collect_sql()
        assert any(
            "CREATE TRIGGER" in s and "handle_new_user" in s for s in sql_stmts
        ), (
            "upgrade() must CREATE TRIGGER handle_new_user; "
            f"found SQL: {sql_stmts}"
        )

    def test_trigger_is_after_insert(self):
        sql_stmts = self._collect_sql()
        trigger_sql = next(
            (s for s in sql_stmts if "CREATE TRIGGER" in s and "handle_new_user" in s),
            None,
        )
        assert trigger_sql is not None, "No CREATE TRIGGER SQL found for handle_new_user"
        upper = trigger_sql.upper()
        assert "AFTER INSERT" in upper, (
            f"Trigger must be AFTER INSERT; got: {trigger_sql!r}"
        )

    def test_trigger_on_auth_users(self):
        sql_stmts = self._collect_sql()
        trigger_sql = next(
            (s for s in sql_stmts if "CREATE TRIGGER" in s and "handle_new_user" in s),
            None,
        )
        assert trigger_sql is not None, "No CREATE TRIGGER SQL found for handle_new_user"
        assert "auth.users" in trigger_sql, (
            f"Trigger must be ON auth.users; got: {trigger_sql!r}"
        )

    def test_trigger_for_each_row(self):
        sql_stmts = self._collect_sql()
        trigger_sql = next(
            (s for s in sql_stmts if "CREATE TRIGGER" in s and "handle_new_user" in s),
            None,
        )
        assert trigger_sql is not None
        upper = trigger_sql.upper()
        assert "FOR EACH ROW" in upper, (
            f"Trigger must be FOR EACH ROW; got: {trigger_sql!r}"
        )

    def test_trigger_executes_handle_new_user(self):
        sql_stmts = self._collect_sql()
        trigger_sql = next(
            (s for s in sql_stmts if "CREATE TRIGGER" in s and "handle_new_user" in s),
            None,
        )
        assert trigger_sql is not None
        upper = trigger_sql.upper()
        assert "EXECUTE FUNCTION" in upper or "EXECUTE PROCEDURE" in upper, (
            f"Trigger must EXECUTE FUNCTION handle_new_user(); got: {trigger_sql!r}"
        )


# ---------------------------------------------------------------------------
# downgrade() — cleanup
# ---------------------------------------------------------------------------

class TestDowngrade:
    """downgrade() must drop the trigger and the function."""

    def _collect_sql(self) -> list[str]:
        mod = _load_migration()
        executed: list[str] = []

        import alembic.op as _op_module
        orig_execute = getattr(_op_module, "execute", None)

        def fake_execute(sql: str) -> None:
            executed.append(sql)

        _op_module.execute = fake_execute
        try:
            mod.downgrade()
        finally:
            if orig_execute is not None:
                _op_module.execute = orig_execute

        return executed

    def test_drops_trigger(self):
        sql_stmts = self._collect_sql()
        combined = "\n".join(sql_stmts).upper()
        assert "DROP TRIGGER" in combined, (
            f"downgrade() must DROP TRIGGER; found SQL: {sql_stmts}"
        )

    def test_drops_function(self):
        sql_stmts = self._collect_sql()
        combined = "\n".join(sql_stmts).upper()
        assert "DROP FUNCTION" in combined and "HANDLE_NEW_USER" in combined, (
            f"downgrade() must DROP FUNCTION handle_new_user; found SQL: {sql_stmts}"
        )
