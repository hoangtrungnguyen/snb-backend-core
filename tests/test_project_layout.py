"""
Tests for Django project layout: settings package, urls package, and Django apps.

RED phase: These tests verify the existence and correctness of the project layout.
"""

import importlib
import importlib.util
import os
import sys

import pytest

WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Ensure worktree root is in sys.path for imports
if WORKTREE_ROOT not in sys.path:
    sys.path.insert(0, WORKTREE_ROOT)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def module_exists(dotted_name: str) -> bool:
    """Return True if the module can be found (without importing it)."""
    try:
        spec = importlib.util.find_spec(dotted_name)
        return spec is not None
    except (ModuleNotFoundError, ValueError):
        return False


def file_exists(*path_parts: str) -> bool:
    return os.path.isfile(os.path.join(WORKTREE_ROOT, *path_parts))


def dir_exists(*path_parts: str) -> bool:
    return os.path.isdir(os.path.join(WORKTREE_ROOT, *path_parts))


# ---------------------------------------------------------------------------
# manage.py
# ---------------------------------------------------------------------------

class TestManagePy:
    def test_manage_py_exists(self):
        assert file_exists("manage.py"), "manage.py must exist at project root"

    def test_manage_py_references_settings(self):
        with open(os.path.join(WORKTREE_ROOT, "manage.py")) as fh:
            content = fh.read()
        assert "spb_core.settings" in content, (
            "manage.py must set DJANGO_SETTINGS_MODULE to spb_core.settings.*"
        )


# ---------------------------------------------------------------------------
# requirements.txt
# ---------------------------------------------------------------------------

class TestRequirementsTxt:
    def test_requirements_txt_exists(self):
        assert file_exists("requirements.txt"), "requirements.txt must exist"

    def test_requirements_contains_django(self):
        with open(os.path.join(WORKTREE_ROOT, "requirements.txt")) as fh:
            content = fh.read().lower()
        assert "django" in content, "requirements.txt must list Django"

    def test_requirements_contains_drf(self):
        with open(os.path.join(WORKTREE_ROOT, "requirements.txt")) as fh:
            content = fh.read().lower()
        assert "djangorestframework" in content, (
            "requirements.txt must list djangorestframework"
        )


# ---------------------------------------------------------------------------
# spb_core/settings/ package
# ---------------------------------------------------------------------------

SETTINGS_FILES = ["__init__.py", "base.py", "local.py", "prod.py"]


class TestSettingsPackage:
    def test_settings_dir_exists(self):
        assert dir_exists("spb_core", "settings"), (
            "spb_core/settings/ directory must exist"
        )

    @pytest.mark.parametrize("filename", SETTINGS_FILES)
    def test_settings_file_exists(self, filename):
        assert file_exists("spb_core", "settings", filename), (
            f"spb_core/settings/{filename} must exist"
        )

    def test_base_settings_has_installed_apps(self):
        path = os.path.join(WORKTREE_ROOT, "spb_core", "settings", "base.py")
        with open(path) as fh:
            content = fh.read()
        assert "INSTALLED_APPS" in content, "base.py must define INSTALLED_APPS"

    def test_base_settings_has_databases(self):
        path = os.path.join(WORKTREE_ROOT, "spb_core", "settings", "base.py")
        with open(path) as fh:
            content = fh.read()
        assert "DATABASES" in content, "base.py must define DATABASES"

    def test_local_settings_imports_base(self):
        path = os.path.join(WORKTREE_ROOT, "spb_core", "settings", "local.py")
        with open(path) as fh:
            content = fh.read()
        assert "base" in content, "local.py must import from base settings"

    def test_prod_settings_imports_base(self):
        path = os.path.join(WORKTREE_ROOT, "spb_core", "settings", "prod.py")
        with open(path) as fh:
            content = fh.read()
        assert "base" in content, "prod.py must import from base settings"

    def test_prod_settings_has_debug_false(self):
        path = os.path.join(WORKTREE_ROOT, "spb_core", "settings", "prod.py")
        with open(path) as fh:
            content = fh.read()
        assert "DEBUG = False" in content, "prod.py must set DEBUG = False"


# ---------------------------------------------------------------------------
# spb_core/urls/ package (or urls.py)
# ---------------------------------------------------------------------------

class TestUrlsPackage:
    def test_urls_structure_exists(self):
        """Either spb_core/urls/ package or spb_core/urls.py must exist."""
        has_package = dir_exists("spb_core", "urls")
        has_module = file_exists("spb_core", "urls.py")
        assert has_package or has_module, (
            "spb_core must have either a urls/ package or a urls.py module"
        )

    def test_urls_has_urlpatterns(self):
        """The root urlconf must define urlpatterns."""
        if dir_exists("spb_core", "urls"):
            path = os.path.join(WORKTREE_ROOT, "spb_core", "urls", "__init__.py")
            if not os.path.isfile(path):
                # try urls/base.py or urls/router.py
                path = os.path.join(WORKTREE_ROOT, "spb_core", "urls", "base.py")
        else:
            path = os.path.join(WORKTREE_ROOT, "spb_core", "urls.py")

        with open(path) as fh:
            content = fh.read()
        assert "urlpatterns" in content, (
            "Root URL conf must define urlpatterns"
        )


# ---------------------------------------------------------------------------
# Django apps
# ---------------------------------------------------------------------------

APPS = ["courts", "bookings", "series", "auth_ext", "notifications", "analytics"]
APP_FILES = ["__init__.py", "apps.py", "models.py", "views.py", "urls.py"]


class TestDjangoApps:
    @pytest.mark.parametrize("app", APPS)
    def test_app_directory_exists(self, app):
        assert dir_exists(app), f"Django app directory '{app}/' must exist"

    @pytest.mark.parametrize("app", APPS)
    @pytest.mark.parametrize("filename", APP_FILES)
    def test_app_file_exists(self, app, filename):
        assert file_exists(app, filename), (
            f"Django app '{app}/{filename}' must exist"
        )

    @pytest.mark.parametrize("app", APPS)
    def test_apps_py_has_appconfig(self, app):
        path = os.path.join(WORKTREE_ROOT, app, "apps.py")
        with open(path) as fh:
            content = fh.read()
        assert "AppConfig" in content, (
            f"{app}/apps.py must define an AppConfig subclass"
        )

    @pytest.mark.parametrize("app", APPS)
    def test_app_registered_in_installed_apps(self, app):
        path = os.path.join(WORKTREE_ROOT, "spb_core", "settings", "base.py")
        with open(path) as fh:
            content = fh.read()
        assert app in content, (
            f"App '{app}' must be listed in INSTALLED_APPS in base.py"
        )
