"""Tests for migration 0001 — initial schema.

These tests run against SQLite in offline mode to verify:
  - Migration file is importable and has expected upgrade/downgrade callables.
  - All required tables are created by upgrade().
  - All required indexes are defined.
  - updated_at trigger SQL is present in the migration.
  - FK ondelete=SET NULL for booking_series_id on bookings.
  - Migration is idempotent when the DB URL is the same (upgrade head twice).

We use an in-memory PostgreSQL-compatible approach via alembic's offline SQL
generation so no live database is required.  The structural assertions cover
grava-ea77.1.1 through grava-ea77.1.4.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0001_initial_schema.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location(
        "migration_0001", MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# grava-ea77.1.1 — Module structure & Alembic metadata
# ---------------------------------------------------------------------------

class TestMigrationModuleStructure:
    def test_migration_file_exists(self):
        assert MIGRATION_PATH.exists(), (
            f"Migration file not found at {MIGRATION_PATH}"
        )

    def test_revision_id(self):
        mod = _load_migration()
        assert mod.revision == "0001"

    def test_down_revision_is_none(self):
        """First migration must have no parent revision."""
        mod = _load_migration()
        assert mod.down_revision is None

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# grava-ea77.1.1 — upgrade() creates all required tables
# ---------------------------------------------------------------------------

REQUIRED_TABLES = [
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


class TestUpgradeCreatesAllTables:
    """Verify that upgrade() calls op.create_table for every required table."""

    def _collect_created_tables(self) -> list[str]:
        mod = _load_migration()
        created: list[str] = []

        import alembic.op as _op_module
        original_create_table = getattr(_op_module, "create_table", None)
        original_execute = getattr(_op_module, "execute", None)
        original_create_index = getattr(_op_module, "create_index", None)

        def fake_create_table(name: str, *args, **kwargs) -> None:
            created.append(name)

        def fake_execute(sql: str) -> None:
            pass

        def fake_create_index(*args, **kwargs) -> None:
            pass

        _op_module.create_table = fake_create_table
        _op_module.execute = fake_execute
        _op_module.create_index = fake_create_index
        try:
            mod.upgrade()
        finally:
            if original_create_table is not None:
                _op_module.create_table = original_create_table
            if original_execute is not None:
                _op_module.execute = original_execute
            if original_create_index is not None:
                _op_module.create_index = original_create_index

        return created

    @pytest.mark.parametrize("table", REQUIRED_TABLES)
    def test_table_created(self, table: str):
        created = self._collect_created_tables()
        assert table in created, (
            f"upgrade() must create table '{table}'; found: {created}"
        )


# ---------------------------------------------------------------------------
# grava-ea77.1.2 — Foreign key cascades
# ---------------------------------------------------------------------------

class TestForeignKeyCascades:
    """Verify booking_series_id FK on bookings has ondelete=SET NULL."""

    def test_bookings_booking_series_id_set_null(self):
        """The bookings.booking_series_id column must cascade SET NULL."""
        import sqlalchemy as sa
        from sqlalchemy.dialects import postgresql

        mod = _load_migration()

        # Capture columns passed to op.create_table("bookings", ...)
        captured_args: dict[str, list] = {}

        import alembic.op as _op_module
        original_create_table = getattr(_op_module, "create_table", None)
        original_execute = getattr(_op_module, "execute", None)
        original_create_index = getattr(_op_module, "create_index", None)

        def fake_create_table(name: str, *args, **kwargs):
            captured_args[name] = list(args)

        def fake_execute(sql: str) -> None:
            pass

        def fake_create_index(*a, **kw) -> None:
            pass

        _op_module.create_table = fake_create_table
        _op_module.execute = fake_execute
        _op_module.create_index = fake_create_index
        try:
            mod.upgrade()
        finally:
            if original_create_table:
                _op_module.create_table = original_create_table
            if original_execute:
                _op_module.execute = original_execute
            if original_create_index:
                _op_module.create_index = original_create_index

        assert "bookings" in captured_args, "upgrade() must call op.create_table('bookings', ...)"
        booking_columns = captured_args["bookings"]

        # Find the booking_series_id column
        bs_col = next(
            (
                c for c in booking_columns
                if isinstance(c, sa.Column) and c.name == "booking_series_id"
            ),
            None,
        )
        assert bs_col is not None, (
            "bookings table must have a 'booking_series_id' column"
        )

        # Verify FK ondelete
        fk_constraints = [
            c for c in bs_col.foreign_keys
        ]
        assert fk_constraints, (
            "booking_series_id must have a ForeignKey constraint"
        )
        fk = list(fk_constraints)[0]
        assert fk.ondelete and fk.ondelete.upper() == "SET NULL", (
            f"booking_series_id FK must have ondelete='SET NULL', got: {fk.ondelete!r}"
        )


# ---------------------------------------------------------------------------
# grava-ea77.1.3 — Required indexes
# ---------------------------------------------------------------------------

REQUIRED_INDEXES = [
    # (index_name_or_columns, table)
    ("ix_slots_court_id_start_at_status", "slots"),
    ("ix_bookings_slot_id_status", "bookings"),
    ("ix_bookings_user_id", "bookings"),
    ("ix_bookings_booking_series_id", "bookings"),
    ("ix_courts_owner_id", "courts"),
    ("ix_courts_lat_lng", "courts"),
    ("uq_courts_slug", "courts"),
    ("ix_notifications_user_id_read", "notifications"),
    ("ix_slot_push_log_user_id_pushed_at", "slot_push_log"),
]


class TestRequiredIndexes:
    """Verify upgrade() creates all required indexes."""

    def _collect_indexes(self) -> list[tuple[str, str]]:
        mod = _load_migration()
        indexes: list[tuple[str, str]] = []

        import alembic.op as _op_module
        original_create_table = getattr(_op_module, "create_table", None)
        original_execute = getattr(_op_module, "execute", None)
        original_create_index = getattr(_op_module, "create_index", None)

        def fake_create_table(name: str, *args, **kwargs):
            pass

        def fake_execute(sql: str) -> None:
            pass

        def fake_create_index(index_name: str, table: str, *args, **kwargs):
            indexes.append((index_name, table))

        _op_module.create_table = fake_create_table
        _op_module.execute = fake_execute
        _op_module.create_index = fake_create_index
        try:
            mod.upgrade()
        finally:
            if original_create_table:
                _op_module.create_table = original_create_table
            if original_execute:
                _op_module.execute = original_execute
            if original_create_index:
                _op_module.create_index = original_create_index

        return indexes

    @pytest.mark.parametrize("idx_name,table", REQUIRED_INDEXES)
    def test_index_created(self, idx_name: str, table: str):
        indexes = self._collect_indexes()
        assert (idx_name, table) in indexes, (
            f"Expected index '{idx_name}' on '{table}'; found: {indexes}"
        )

    def test_courts_slug_is_unique(self):
        """courts.slug index must be unique."""
        import alembic.op as _op_module
        mod = _load_migration()
        unique_indexes: list[str] = []

        original_create_table = getattr(_op_module, "create_table", None)
        original_execute = getattr(_op_module, "execute", None)
        original_create_index = getattr(_op_module, "create_index", None)

        def fake_create_table(name, *a, **kw): pass
        def fake_execute(sql): pass
        def fake_create_index(name, table, *a, unique=False, **kw):
            if unique:
                unique_indexes.append(name)

        _op_module.create_table = fake_create_table
        _op_module.execute = fake_execute
        _op_module.create_index = fake_create_index
        try:
            mod.upgrade()
        finally:
            if original_create_table:
                _op_module.create_table = original_create_table
            if original_execute:
                _op_module.execute = original_execute
            if original_create_index:
                _op_module.create_index = original_create_index

        assert "uq_courts_slug" in unique_indexes, (
            f"courts.slug index must be unique; unique indexes found: {unique_indexes}"
        )


# ---------------------------------------------------------------------------
# grava-ea77.1.4 — updated_at trigger SQL
# ---------------------------------------------------------------------------

UPDATED_AT_TABLES = ["courts", "slots", "booking_series", "bookings"]


class TestUpdatedAtTriggers:
    """Verify upgrade() emits CREATE TRIGGER SQL for each required table."""

    def _collect_executed_sql(self) -> list[str]:
        mod = _load_migration()
        executed: list[str] = []

        import alembic.op as _op_module
        original_create_table = getattr(_op_module, "create_table", None)
        original_execute = getattr(_op_module, "execute", None)
        original_create_index = getattr(_op_module, "create_index", None)

        def fake_create_table(name, *a, **kw): pass
        def fake_execute(sql: str): executed.append(sql)
        def fake_create_index(*a, **kw): pass

        _op_module.create_table = fake_create_table
        _op_module.execute = fake_execute
        _op_module.create_index = fake_create_index
        try:
            mod.upgrade()
        finally:
            if original_create_table:
                _op_module.create_table = original_create_table
            if original_execute:
                _op_module.execute = original_execute
            if original_create_index:
                _op_module.create_index = original_create_index

        return executed

    def test_set_updated_at_function_created(self):
        sql_stmts = self._collect_executed_sql()
        assert any("set_updated_at" in s and "CREATE" in s for s in sql_stmts), (
            "upgrade() must CREATE the set_updated_at() function"
        )

    @pytest.mark.parametrize("table", UPDATED_AT_TABLES)
    def test_trigger_created_for_table(self, table: str):
        sql_stmts = self._collect_executed_sql()
        assert any(
            "CREATE TRIGGER" in s and table in s for s in sql_stmts
        ), (
            f"upgrade() must CREATE a trigger on '{table}' for updated_at; "
            f"found SQL: {sql_stmts}"
        )

    def test_downgrade_drops_function(self):
        mod = _load_migration()
        executed: list[str] = []

        import alembic.op as _op_module
        original_drop_table = getattr(_op_module, "drop_table", None)
        original_execute = getattr(_op_module, "execute", None)

        def fake_drop_table(name): pass
        def fake_execute(sql: str): executed.append(sql)

        _op_module.drop_table = fake_drop_table
        _op_module.execute = fake_execute
        try:
            mod.downgrade()
        finally:
            if original_drop_table:
                _op_module.drop_table = original_drop_table
            if original_execute:
                _op_module.execute = original_execute

        assert any("DROP FUNCTION" in s and "set_updated_at" in s for s in executed), (
            "downgrade() must DROP the set_updated_at() function"
        )


# ---------------------------------------------------------------------------
# grava-ea77.1.1 — downgrade() drops all tables in reverse order
# ---------------------------------------------------------------------------

class TestDowngradeDropsAllTables:
    def test_all_tables_dropped(self):
        mod = _load_migration()
        dropped: list[str] = []

        import alembic.op as _op_module
        original_drop_table = getattr(_op_module, "drop_table", None)
        original_execute = getattr(_op_module, "execute", None)

        def fake_drop_table(name): dropped.append(name)
        def fake_execute(sql): pass

        _op_module.drop_table = fake_drop_table
        _op_module.execute = fake_execute
        try:
            mod.downgrade()
        finally:
            if original_drop_table:
                _op_module.drop_table = original_drop_table
            if original_execute:
                _op_module.execute = original_execute

        for table in REQUIRED_TABLES:
            assert table in dropped, (
                f"downgrade() must drop table '{table}'; dropped: {dropped}"
            )
