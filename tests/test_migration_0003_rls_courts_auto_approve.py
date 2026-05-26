"""Tests for migration 0003 — column-level access on ``courts.auto_approve_single``.

These tests run no live DB. They intercept ``alembic.op.execute`` and assert
that the migration emits the required ``REVOKE`` / ``GRANT`` statements for
both ``upgrade()`` and ``downgrade()``.

Covers task grava-ea77.2.2.
"""

from __future__ import annotations

import importlib.util
import re
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "0003_rls_courts_auto_approve_column.py"


def _load_migration() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(
        "migration_0003_rls_courts_auto_approve_column", MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_sql(direction: str) -> list[str]:
    """Run ``direction`` ('upgrade' / 'downgrade') and capture op.execute SQL."""
    mod = _load_migration()
    executed: list[str] = []

    import alembic.op as _op_module
    original_execute = getattr(_op_module, "execute", None)

    def fake_execute(sql: str) -> None:
        executed.append(sql)

    _op_module.execute = fake_execute
    try:
        getattr(mod, direction)()
    finally:
        if original_execute is not None:
            _op_module.execute = original_execute

    return executed


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().upper()


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
        assert mod.revision == "0003"

    def test_down_revision_is_0002(self):
        mod = _load_migration()
        assert mod.down_revision == "0002", (
            "0003 must chain off 0002 (RLS on courts) so its column GRANTs "
            "are applied after the row-level RLS is enabled"
        )


# ---------------------------------------------------------------------------
# upgrade() — REVOKE from PUBLIC + anon, GRANT to authenticated
# ---------------------------------------------------------------------------

REVOKE_FROM = ["PUBLIC", "ANON"]
GRANT_TO_AUTHENTICATED = ["SELECT", "UPDATE"]


class TestUpgradeRevokesPublicAndAnon:
    """upgrade() must revoke SELECT + UPDATE on the column from PUBLIC + anon."""

    @pytest.mark.parametrize("role", REVOKE_FROM)
    @pytest.mark.parametrize("priv", GRANT_TO_AUTHENTICATED)
    def test_revoke(self, role: str, priv: str):
        statements = [_normalize(s) for s in _collect_sql("upgrade")]
        # e.g. "REVOKE SELECT (AUTO_APPROVE_SINGLE) ON COURTS FROM PUBLIC"
        match = next(
            (
                s for s in statements
                if "REVOKE" in s
                and priv in s
                and "AUTO_APPROVE_SINGLE" in s
                and "ON COURTS" in s
                and f"FROM {role}" in s
            ),
            None,
        )
        assert match is not None, (
            f"upgrade() must REVOKE {priv} (auto_approve_single) ON courts FROM {role}; "
            f"got: {statements}"
        )


class TestUpgradeGrantsAuthenticated:
    """upgrade() must re-grant SELECT + UPDATE on the column to authenticated."""

    @pytest.mark.parametrize("priv", GRANT_TO_AUTHENTICATED)
    def test_grant_authenticated(self, priv: str):
        statements = [_normalize(s) for s in _collect_sql("upgrade")]
        match = next(
            (
                s for s in statements
                if "GRANT" in s
                and priv in s
                and "AUTO_APPROVE_SINGLE" in s
                and "ON COURTS" in s
                and "TO AUTHENTICATED" in s
            ),
            None,
        )
        assert match is not None, (
            f"upgrade() must GRANT {priv} (auto_approve_single) ON courts TO authenticated; "
            f"got: {statements}"
        )


class TestUpgradeDoesNotGrantToAnon:
    """Regression: upgrade() must NOT grant the column to anon or PUBLIC."""

    @pytest.mark.parametrize("role", ["ANON", "PUBLIC"])
    @pytest.mark.parametrize("priv", GRANT_TO_AUTHENTICATED)
    def test_no_grant_to_anon_or_public(self, role: str, priv: str):
        statements = [_normalize(s) for s in _collect_sql("upgrade")]
        bad = [
            s for s in statements
            if "GRANT" in s
            and priv in s
            and "AUTO_APPROVE_SINGLE" in s
            and "ON COURTS" in s
            and f"TO {role}" in s
        ]
        assert not bad, (
            f"upgrade() must NOT GRANT {priv} on auto_approve_single TO {role}; "
            f"offending: {bad}"
        )


# ---------------------------------------------------------------------------
# downgrade() — restore broad access
# ---------------------------------------------------------------------------

class TestDowngradeRevokesAuthenticated:
    @pytest.mark.parametrize("priv", GRANT_TO_AUTHENTICATED)
    def test_revoke_authenticated(self, priv: str):
        statements = [_normalize(s) for s in _collect_sql("downgrade")]
        match = next(
            (
                s for s in statements
                if "REVOKE" in s
                and priv in s
                and "AUTO_APPROVE_SINGLE" in s
                and "ON COURTS" in s
                and "FROM AUTHENTICATED" in s
            ),
            None,
        )
        assert match is not None, (
            f"downgrade() must REVOKE {priv} FROM authenticated; got: {statements}"
        )


class TestDowngradeRestoresPublicAndAnon:
    @pytest.mark.parametrize("role", ["ANON", "PUBLIC"])
    @pytest.mark.parametrize("priv", GRANT_TO_AUTHENTICATED)
    def test_restore_default_grants(self, role: str, priv: str):
        statements = [_normalize(s) for s in _collect_sql("downgrade")]
        match = next(
            (
                s for s in statements
                if "GRANT" in s
                and priv in s
                and "AUTO_APPROVE_SINGLE" in s
                and "ON COURTS" in s
                and f"TO {role}" in s
            ),
            None,
        )
        assert match is not None, (
            f"downgrade() must restore {priv} TO {role}; got: {statements}"
        )
