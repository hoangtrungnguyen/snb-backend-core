"""
Tests for the settings package structure (grava-ea77.3.2).

Verifies:
- settings package exists (base, local, prod modules)
- base.py uses django-environ
- SECRET_KEY raises ImproperlyConfigured when env var is missing
- local.py has DEBUG=True and SQLite default
- prod.py has DEBUG=False, security headers, DATABASE_URL from env
"""

import importlib
import os
import sys
import types
from unittest import mock

import pytest


WORKTREE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ensure_spb_core_on_path():
    """Add the worktree root to sys.path so spb_core is importable."""
    if WORKTREE not in sys.path:
        sys.path.insert(0, WORKTREE)


def _clean_settings_modules():
    """Remove cached spb_core.settings modules so tests get a fresh import."""
    to_remove = [k for k in sys.modules if k.startswith("spb_core.settings") or k == "spb_core.settings"]
    for key in to_remove:
        del sys.modules[key]


class TestSettingsPackageStructure:
    def setup_method(self):
        _ensure_spb_core_on_path()

    def test_settings_package_exists(self):
        """spb_core/settings/ must be a proper Python package."""
        settings_dir = os.path.join(WORKTREE, "spb_core", "settings")
        assert os.path.isdir(settings_dir), "spb_core/settings/ directory missing"
        assert os.path.isfile(os.path.join(settings_dir, "__init__.py")), "settings/__init__.py missing"

    def test_base_module_exists(self):
        assert os.path.isfile(os.path.join(WORKTREE, "spb_core", "settings", "base.py"))

    def test_local_module_exists(self):
        assert os.path.isfile(os.path.join(WORKTREE, "spb_core", "settings", "local.py"))

    def test_prod_module_exists(self):
        assert os.path.isfile(os.path.join(WORKTREE, "spb_core", "settings", "prod.py"))

    def test_base_uses_environ(self):
        """base.py must import environ from django-environ."""
        base_path = os.path.join(WORKTREE, "spb_core", "settings", "base.py")
        with open(base_path) as f:
            src = f.read()
        assert "import environ" in src, "base.py must import environ"
        assert "environ.Env(" in src, "base.py must instantiate environ.Env"


class TestSecretKeyRequired:
    def setup_method(self):
        _ensure_spb_core_on_path()
        _clean_settings_modules()

    def teardown_method(self):
        _clean_settings_modules()

    def test_secret_key_raises_when_missing(self):
        """Importing base settings without SECRET_KEY in env must raise ImproperlyConfigured."""
        import environ as environ_lib
        from django.core.exceptions import ImproperlyConfigured

        env_without_secret = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
        # Also clear DATABASE_URL to avoid psycopg2 deps during test
        env_without_secret.pop("DATABASE_URL", None)

        # Suppress read_env so the .env file on disk cannot inject SECRET_KEY
        with mock.patch.dict(os.environ, env_without_secret, clear=True), \
             mock.patch.object(environ_lib.Env, "read_env", staticmethod(lambda *a, **kw: None)):
            _clean_settings_modules()
            with pytest.raises((ImproperlyConfigured, KeyError)):
                import spb_core.settings.base  # noqa: F401


class TestLocalSettings:
    def setup_method(self):
        _ensure_spb_core_on_path()
        _clean_settings_modules()

    def teardown_method(self):
        _clean_settings_modules()

    def test_local_has_debug_true(self):
        """local.py must set DEBUG = True."""
        local_path = os.path.join(WORKTREE, "spb_core", "settings", "local.py")
        with open(local_path) as f:
            src = f.read()
        assert "DEBUG = True" in src, "local.py must set DEBUG = True"

    def test_local_imports_base(self):
        """local.py must import from base."""
        local_path = os.path.join(WORKTREE, "spb_core", "settings", "local.py")
        with open(local_path) as f:
            src = f.read()
        assert "from .base import" in src or "from spb_core.settings.base import" in src, \
            "local.py must import from base"

    def test_local_uses_sqlite_default(self):
        """local.py must reference sqlite as the default database."""
        local_path = os.path.join(WORKTREE, "spb_core", "settings", "local.py")
        with open(local_path) as f:
            src = f.read()
        assert "sqlite" in src.lower(), "local.py must reference sqlite as development default"


class TestProdSettings:
    def test_prod_has_debug_false(self):
        """prod.py must set DEBUG = False."""
        prod_path = os.path.join(WORKTREE, "spb_core", "settings", "prod.py")
        with open(prod_path) as f:
            src = f.read()
        assert "DEBUG = False" in src, "prod.py must set DEBUG = False"

    def test_prod_imports_base(self):
        """prod.py must import from base."""
        prod_path = os.path.join(WORKTREE, "spb_core", "settings", "prod.py")
        with open(prod_path) as f:
            src = f.read()
        assert "from .base import" in src or "from spb_core.settings.base import" in src, \
            "prod.py must import from base"

    def test_prod_has_security_headers(self):
        """prod.py must define security-hardening settings."""
        prod_path = os.path.join(WORKTREE, "spb_core", "settings", "prod.py")
        with open(prod_path) as f:
            src = f.read()
        assert "SECURE_SSL_REDIRECT" in src, "prod.py must set SECURE_SSL_REDIRECT"
        assert "CSRF_COOKIE_SECURE" in src, "prod.py must set CSRF_COOKIE_SECURE"
        assert "SESSION_COOKIE_SECURE" in src, "prod.py must set SESSION_COOKIE_SECURE"

    def test_prod_database_url_from_env(self):
        """prod.py or base.py must load DATABASE_URL from env for prod."""
        prod_path = os.path.join(WORKTREE, "spb_core", "settings", "prod.py")
        base_path = os.path.join(WORKTREE, "spb_core", "settings", "base.py")
        with open(prod_path) as f:
            prod_src = f.read()
        with open(base_path) as f:
            base_src = f.read()
        assert "DATABASE_URL" in prod_src or "DATABASE_URL" in base_src, \
            "DATABASE_URL must be loaded from env (in prod.py or base.py)"
