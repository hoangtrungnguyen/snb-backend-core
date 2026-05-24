"""
Tests for Supabase connection-pooler database settings (grava-ea77.3.4).

Verifies:
- prod.py sets CONN_MAX_AGE=60 in DATABASES['default']
- prod.py sets DISABLE_SERVER_SIDE_CURSORS=True (required for PgBouncer transaction mode)
- prod.py DATABASE_URL is required (no default — raises when missing)
- local.py provides a SQLite default so dev works without DATABASE_URL
- base.py does NOT hardcode CONN_MAX_AGE/DISABLE_SERVER_SIDE_CURSORS
  (they belong in prod where they matter; local dev can differ)
"""

import os


WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read(relative_path):
    with open(os.path.join(WORKTREE, relative_path)) as f:
        return f.read()


class TestProdConnectionPoolerSettings:
    """prod.py must configure CONN_MAX_AGE and DISABLE_SERVER_SIDE_CURSORS."""

    def test_prod_conn_max_age_60(self):
        """prod.py DATABASES must set CONN_MAX_AGE=60."""
        src = _read("spb_core/settings/prod.py")
        assert "CONN_MAX_AGE" in src, (
            "prod.py must set CONN_MAX_AGE in DATABASES (required for Supabase pooler)"
        )
        assert "60" in src, (
            "prod.py CONN_MAX_AGE must be 60"
        )

    def test_prod_disable_server_side_cursors(self):
        """prod.py must set DISABLE_SERVER_SIDE_CURSORS=True for PgBouncer transaction mode."""
        src = _read("spb_core/settings/prod.py")
        assert "DISABLE_SERVER_SIDE_CURSORS" in src, (
            "prod.py must set DISABLE_SERVER_SIDE_CURSORS=True "
            "(server-side cursors don't work in PgBouncer transaction-pooling mode)"
        )
        assert "True" in src, (
            "prod.py DISABLE_SERVER_SIDE_CURSORS must be True"
        )

    def test_prod_database_url_required_no_default(self):
        """In prod.py, env.db('DATABASE_URL') must NOT have a fallback default."""
        src = _read("spb_core/settings/prod.py")
        # Must reference DATABASE_URL
        assert "DATABASE_URL" in src, "prod.py must load DATABASE_URL from env"
        # Must NOT have sqlite default in prod
        assert "sqlite" not in src.lower(), (
            "prod.py must not fall back to SQLite — DATABASE_URL is required in production"
        )

    def test_prod_database_block_present(self):
        """prod.py must define DATABASES with a 'default' key."""
        src = _read("spb_core/settings/prod.py")
        assert "DATABASES" in src, "prod.py must define DATABASES"
        assert '"default"' in src or "'default'" in src, (
            "prod.py DATABASES must have a 'default' key"
        )


class TestLocalSettingsDevFriendly:
    """local.py must keep a SQLite (or local PG) default so dev needs no DATABASE_URL."""

    def test_local_has_sqlite_default(self):
        """local.py must provide a SQLite default for DATABASE_URL-free dev."""
        src = _read("spb_core/settings/local.py")
        assert "sqlite" in src.lower(), (
            "local.py must use SQLite as the default database for local development"
        )

    def test_local_database_defined(self):
        """local.py must define DATABASES."""
        src = _read("spb_core/settings/local.py")
        assert "DATABASES" in src, "local.py must define DATABASES"


class TestProdEnvDbCallShape:
    """prod.py env.db() call must use the correct DATABASE_URL key."""

    def test_prod_uses_env_db(self):
        """prod.py must use env.db() to load DATABASE_URL (django-environ pattern)."""
        src = _read("spb_core/settings/prod.py")
        assert "env.db(" in src, "prod.py must call env.db() to load DATABASE_URL"

    def test_prod_env_db_references_database_url(self):
        """prod.py env.db() call must reference 'DATABASE_URL'."""
        src = _read("spb_core/settings/prod.py")
        assert 'env.db("DATABASE_URL")' in src or "env.db('DATABASE_URL')" in src, (
            "prod.py must call env.db('DATABASE_URL') with no default"
        )
