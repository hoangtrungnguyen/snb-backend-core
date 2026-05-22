"""Tests for migration 0002 — RLS policies on the `courts` table.

These tests exercise the migration module structurally (no live DB) by
intercepting `alembic.op.execute` and asserting that the required SQL
statements are issued by ``upgrade()`` and ``downgrade()``.

Covers task grava-ea77.2.1:

    `courts`: SELECT public for status = approved;
              INSERT/UPDATE/DELETE only owner_id = auth.uid()
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
MIGRATION_PATH = VERSIONS_DIR / "0002_rls_courts.py"


def _load_migration() -> types.ModuleType:
    """Dynamically import the migration module by file path."""
    spec = importlib.util.spec_from_file_location(
        "migration_0002_rls_courts", MIGRATION_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_upgrade_sql() -> list[str]:
    """Run ``upgrade()`` with a stubbed ``op.execute`` and return SQL strings."""
    mod = _load_migration()
    executed: list[str] = []

    import alembic.op as _op_module

    original_execute = getattr(_op_module, "execute", None)

    def fake_execute(sql: str) -> None:
        executed.append(sql)

    _op_module.execute = fake_execute
    try:
        mod.upgrade()
    finally:
        if original_execute is not None:
            _op_module.execute = original_execute

    return executed


def _collect_downgrade_sql() -> list[str]:
    """Run ``downgrade()`` with a stubbed ``op.execute`` and return SQL strings."""
    mod = _load_migration()
    executed: list[str] = []

    import alembic.op as _op_module

    original_execute = getattr(_op_module, "execute", None)

    def fake_execute(sql: str) -> None:
        executed.append(sql)

    _op_module.execute = fake_execute
    try:
        mod.downgrade()
    finally:
        if original_execute is not None:
            _op_module.execute = original_execute

    return executed


def _normalize(sql: str) -> str:
    """Collapse whitespace so we can match SQL fragments order-independently."""
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
        assert mod.revision == "0002"

    def test_down_revision_is_0001(self):
        mod = _load_migration()
        assert mod.down_revision == "0001", (
            "0002 must chain off 0001 so Alembic applies them in order"
        )

    def test_upgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.upgrade)

    def test_downgrade_callable(self):
        mod = _load_migration()
        assert callable(mod.downgrade)


# ---------------------------------------------------------------------------
# upgrade() — RLS enable + required policies
# ---------------------------------------------------------------------------

class TestUpgradeEnablesRLS:
    def test_enable_row_level_security(self):
        sql = " ".join(_normalize(s) for s in _collect_upgrade_sql())
        assert "ALTER TABLE COURTS ENABLE ROW LEVEL SECURITY" in sql, (
            "upgrade() must issue ALTER TABLE courts ENABLE ROW LEVEL SECURITY"
        )


REQUIRED_POLICIES = [
    # (policy_name, target_command)
    ("courts_select_public_approved", "SELECT"),
    ("courts_insert_owner", "INSERT"),
    ("courts_update_owner", "UPDATE"),
    ("courts_delete_owner", "DELETE"),
]


class TestUpgradeCreatesPolicies:
    @pytest.mark.parametrize("policy,command", REQUIRED_POLICIES)
    def test_policy_created(self, policy: str, command: str):
        statements = [_normalize(s) for s in _collect_upgrade_sql()]
        match = next(
            (
                s for s in statements
                if "CREATE POLICY" in s
                and policy.upper() in s
                and f"FOR {command}" in s
                and "ON COURTS" in s
            ),
            None,
        )
        assert match is not None, (
            f"upgrade() must CREATE POLICY {policy} FOR {command} ON courts; "
            f"got statements: {statements}"
        )

    def test_select_public_policy_filters_approved(self):
        statements = [_normalize(s) for s in _collect_upgrade_sql()]
        select_stmt = next(
            (s for s in statements if "COURTS_SELECT_PUBLIC_APPROVED" in s),
            None,
        )
        assert select_stmt is not None
        # USING (status = 'approved') — normalized to upper-case
        assert "USING (STATUS = 'APPROVED')" in select_stmt, (
            "public SELECT policy must filter on status = 'approved'; "
            f"got: {select_stmt}"
        )

    def test_select_public_policy_targets_anon_and_authenticated(self):
        statements = [_normalize(s) for s in _collect_upgrade_sql()]
        select_stmt = next(
            (s for s in statements if "COURTS_SELECT_PUBLIC_APPROVED" in s),
            None,
        )
        assert select_stmt is not None
        assert "TO ANON, AUTHENTICATED" in select_stmt, (
            "public SELECT policy must grant access to anon + authenticated; "
            f"got: {select_stmt}"
        )

    @pytest.mark.parametrize("policy", [
        "courts_insert_owner",
        "courts_update_owner",
        "courts_delete_owner",
    ])
    def test_owner_policies_use_auth_uid(self, policy: str):
        statements = [_normalize(s) for s in _collect_upgrade_sql()]
        match = next(
            (s for s in statements if policy.upper() in s),
            None,
        )
        assert match is not None, f"missing policy {policy}"
        assert "OWNER_ID = AUTH.UID()" in match, (
            f"policy {policy} must restrict on owner_id = auth.uid(); "
            f"got: {match}"
        )

    def test_insert_policy_uses_with_check(self):
        statements = [_normalize(s) for s in _collect_upgrade_sql()]
        match = next(
            (s for s in statements if "COURTS_INSERT_OWNER" in s),
            None,
        )
        assert match is not None
        assert "WITH CHECK" in match, (
            "INSERT policy must use WITH CHECK to validate new rows; "
            f"got: {match}"
        )

    def test_update_policy_uses_both_using_and_with_check(self):
        statements = [_normalize(s) for s in _collect_upgrade_sql()]
        match = next(
            (s for s in statements if "COURTS_UPDATE_OWNER" in s),
            None,
        )
        assert match is not None
        assert "USING" in match, "UPDATE policy needs USING clause"
        assert "WITH CHECK" in match, (
            "UPDATE policy needs WITH CHECK so ownership cannot be reassigned"
        )

    def test_delete_policy_uses_using_clause(self):
        statements = [_normalize(s) for s in _collect_upgrade_sql()]
        match = next(
            (s for s in statements if "COURTS_DELETE_OWNER" in s),
            None,
        )
        assert match is not None
        assert "USING" in match, "DELETE policy needs a USING clause"


# ---------------------------------------------------------------------------
# downgrade() — drops policies and disables RLS
# ---------------------------------------------------------------------------

class TestDowngrade:
    @pytest.mark.parametrize("policy,_command", REQUIRED_POLICIES)
    def test_drops_each_policy(self, policy: str, _command: str):
        statements = [_normalize(s) for s in _collect_downgrade_sql()]
        match = next(
            (
                s for s in statements
                if "DROP POLICY" in s
                and policy.upper() in s
                and "ON COURTS" in s
            ),
            None,
        )
        assert match is not None, (
            f"downgrade() must DROP POLICY {policy} ON courts; "
            f"got statements: {statements}"
        )

    def test_disables_row_level_security(self):
        statements = [_normalize(s) for s in _collect_downgrade_sql()]
        match = next(
            (
                s for s in statements
                if "ALTER TABLE COURTS DISABLE ROW LEVEL SECURITY" in s
            ),
            None,
        )
        assert match is not None, (
            "downgrade() must DISABLE ROW LEVEL SECURITY on courts; "
            f"got: {statements}"
        )

    def test_drop_policy_uses_if_exists(self):
        """Idempotency: re-running downgrade should not error if policies are
        already gone. ``DROP POLICY IF EXISTS`` is the standard pattern."""
        statements = [_normalize(s) for s in _collect_downgrade_sql()]
        drop_stmts = [s for s in statements if "DROP POLICY" in s]
        assert drop_stmts, "no DROP POLICY statements emitted"
        for stmt in drop_stmts:
            assert "IF EXISTS" in stmt, (
                f"DROP POLICY should be IF EXISTS for idempotency; got: {stmt}"
            )
