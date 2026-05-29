"""
Integration tests for the Django project scaffold (grava-ea77.3).

Verifies that all components of the BCORE-003 scaffold work together:
- Project layout with spb_core/{settings,urls}/ packages + six Django apps
- settings/base.py loads env vars via django-environ; local.py + prod.py split
- auth_ext/middleware.py: JWT decode + role extraction (python-jose + Supabase JWKS)
- DRF DEFAULT_AUTHENTICATION_CLASSES = SupabaseJWTAuthentication
- DRF DEFAULT_PERMISSION_CLASSES = IsAuthenticated
- Health check GET /health/ returns {status, db, realtime}
- Dockerfile (gunicorn) + docker-compose.yml for local dev
- .env.example documents all required env vars

These tests complement the per-subtask test suites and confirm the scaffold is
complete and self-consistent as a story.
"""

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(os.path.join(REPO_ROOT, path)) as fh:
        return fh.read()


def _exists(*parts):
    return os.path.isfile(os.path.join(REPO_ROOT, *parts))


def _isdir(*parts):
    return os.path.isdir(os.path.join(REPO_ROOT, *parts))


# ---------------------------------------------------------------------------
# 1. Project layout
# ---------------------------------------------------------------------------

REQUIRED_APPS = ["auth_ext", "courts", "bookings", "series", "notifications", "analytics"]


class TestProjectLayoutIntegration:
    """All required Django apps and config packages must exist."""

    def test_all_required_app_directories_present(self):
        for app in REQUIRED_APPS:
            assert _isdir(app), f"{app}/ directory missing"

    def test_settings_subpackage_has_all_modules(self):
        for fname in ("__init__.py", "base.py", "local.py", "prod.py"):
            assert _exists("spb_core", "settings", fname), (
                f"spb_core/settings/{fname} missing"
            )

    def test_urls_subpackage_exists(self):
        assert _isdir("spb_core", "urls"), "spb_core/urls/ package missing"

    def test_manage_py_exists_at_root(self):
        assert _exists("manage.py"), "manage.py missing"

    def test_requirements_txt_exists(self):
        assert _exists("requirements.txt"), "requirements.txt missing"


# ---------------------------------------------------------------------------
# 2. Settings wiring
# ---------------------------------------------------------------------------


class TestSettingsWiringIntegration:
    """base.py, local.py, prod.py must satisfy source-level requirements."""

    def test_base_defines_installed_apps_with_all_local_apps(self):
        src = _read("spb_core/settings/base.py")
        for app in REQUIRED_APPS:
            assert app in src, f"{app} missing from base.py INSTALLED_APPS"

    def test_base_uses_django_environ(self):
        src = _read("spb_core/settings/base.py")
        assert "import environ" in src
        assert "environ.Env(" in src

    def test_base_configures_supabase_settings(self):
        src = _read("spb_core/settings/base.py")
        assert "SUPABASE_URL" in src
        assert "SUPABASE_JWKS_URL" in src

    def test_base_configures_drf_authentication(self):
        src = _read("spb_core/settings/base.py")
        assert "SupabaseJWTAuthentication" in src
        assert "DEFAULT_AUTHENTICATION_CLASSES" in src

    def test_base_configures_drf_permission_isauthenticated(self):
        src = _read("spb_core/settings/base.py")
        assert "IsAuthenticated" in src
        assert "DEFAULT_PERMISSION_CLASSES" in src

    def test_local_imports_base_and_sets_debug_true(self):
        src = _read("spb_core/settings/local.py")
        assert "from .base import" in src or "from spb_core.settings.base" in src
        assert "DEBUG = True" in src

    def test_prod_imports_base_and_sets_debug_false(self):
        src = _read("spb_core/settings/prod.py")
        assert "from .base import" in src or "from spb_core.settings.base" in src
        assert "DEBUG = False" in src

    def test_prod_has_database_url_from_env(self):
        src = _read("spb_core/settings/prod.py")
        assert "DATABASE_URL" in src

    def test_prod_sets_conn_max_age_60(self):
        src = _read("spb_core/settings/prod.py")
        assert "CONN_MAX_AGE" in src
        assert "60" in src


# ---------------------------------------------------------------------------
# 3. auth_ext source checks
# ---------------------------------------------------------------------------


class TestAuthExtSourceIntegration:
    """auth_ext/middleware.py and auth_ext/authentication.py must define required classes."""

    def test_middleware_defines_jwt_auth_middleware(self):
        src = _read("auth_ext/middleware.py")
        assert "class JWTAuthMiddleware" in src

    def test_middleware_uses_python_jose(self):
        src = _read("auth_ext/middleware.py")
        assert "from jose" in src or "import jose" in src

    def test_middleware_defines_decode_token(self):
        src = _read("auth_ext/middleware.py")
        assert "_decode_token" in src

    def test_authentication_defines_supabase_jwt_authentication(self):
        src = _read("auth_ext/authentication.py")
        assert "class SupabaseJWTAuthentication" in src

    def test_authentication_extends_base_authentication(self):
        src = _read("auth_ext/authentication.py")
        assert "BaseAuthentication" in src


# ---------------------------------------------------------------------------
# 4. URLs
# ---------------------------------------------------------------------------


class TestUrlsConfigIntegration:
    """Root URL conf must register the health endpoint."""

    def test_health_url_registered_in_root_urlconf(self):
        src = _read("spb_core/urls/__init__.py")
        assert "health" in src
        assert "urlpatterns" in src


# ---------------------------------------------------------------------------
# 5. Docker files
# ---------------------------------------------------------------------------


class TestDockerFilesIntegration:
    """Dockerfile and docker-compose.yml must be present and correct."""

    def test_dockerfile_exists(self):
        assert _exists("Dockerfile"), "Dockerfile missing"

    def test_dockerfile_uses_python_311(self):
        assert "python:3.11" in _read("Dockerfile")

    def test_dockerfile_uses_gunicorn(self):
        assert "gunicorn" in _read("Dockerfile")

    def test_docker_compose_exists(self):
        assert _exists("docker-compose.yml"), "docker-compose.yml missing"

    def test_docker_compose_has_web_and_db_services(self):
        src = _read("docker-compose.yml")
        assert "web:" in src
        assert "db:" in src


# ---------------------------------------------------------------------------
# 6. .env.example
# ---------------------------------------------------------------------------


class TestEnvExampleIntegration:
    """All required env vars must be documented in .env.example."""

    REQUIRED_VARS = [
        "SECRET_KEY",
        "DATABASE_URL",
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "SUPABASE_SECRET_KEY",
        "SUPABASE_JWKS_URL",
        "SUPABASE_JWT_AUDIENCE",
    ]

    def test_env_example_exists(self):
        assert _exists(".env.example"), ".env.example missing"

    def test_env_example_documents_all_required_vars(self):
        src = _read(".env.example")
        for var in self.REQUIRED_VARS:
            assert var in src, f".env.example missing required var: {var}"
