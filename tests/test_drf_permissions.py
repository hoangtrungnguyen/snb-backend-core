"""
Tests for DRF DEFAULT_PERMISSION_CLASSES configuration (grava-ea77.3.6).

Verifies that REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] is set to
["rest_framework.permissions.IsAuthenticated"] in spb_core/settings/base.py.
"""

from __future__ import annotations

import os
from pathlib import Path


BASE_SETTINGS_PATH = (
    Path(__file__).resolve().parents[1] / "spb_core" / "settings" / "base.py"
)


class TestDRFPermissionSettingsFile:
    """Structural tests: verify base.py declares the correct permission class."""

    def test_base_settings_file_exists(self):
        assert BASE_SETTINGS_PATH.exists(), (
            f"spb_core/settings/base.py not found at {BASE_SETTINGS_PATH}"
        )

    def test_rest_framework_key_present(self):
        content = BASE_SETTINGS_PATH.read_text()
        assert "REST_FRAMEWORK" in content, (
            "base.py must define a REST_FRAMEWORK dict"
        )

    def test_default_permission_classes_key_present(self):
        content = BASE_SETTINGS_PATH.read_text()
        assert "DEFAULT_PERMISSION_CLASSES" in content, (
            "REST_FRAMEWORK must include a DEFAULT_PERMISSION_CLASSES key"
        )

    def test_is_authenticated_permission_class_present(self):
        content = BASE_SETTINGS_PATH.read_text()
        assert "rest_framework.permissions.IsAuthenticated" in content, (
            "DEFAULT_PERMISSION_CLASSES must include "
            "'rest_framework.permissions.IsAuthenticated'"
        )


class TestDRFPermissionSettingsModule:
    """Import-level tests: verify the setting value at runtime."""

    def test_rest_framework_has_default_permission_classes(self):
        import importlib.util
        import sys

        spec = importlib.util.spec_from_file_location(
            "spb_core_settings_base", BASE_SETTINGS_PATH
        )
        # base.py uses django-environ; provide a minimal SECRET_KEY env var
        os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert hasattr(mod, "REST_FRAMEWORK"), (
            "base.py must define REST_FRAMEWORK at module level"
        )
        permission_classes = mod.REST_FRAMEWORK.get("DEFAULT_PERMISSION_CLASSES", [])
        assert "rest_framework.permissions.IsAuthenticated" in permission_classes, (
            f"DEFAULT_PERMISSION_CLASSES must contain IsAuthenticated; got {permission_classes}"
        )

    def test_default_permission_classes_is_list_with_one_entry(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "spb_core_settings_base_v2", BASE_SETTINGS_PATH
        )
        os.environ.setdefault("SECRET_KEY", "test-secret-key-for-unit-tests")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        permission_classes = mod.REST_FRAMEWORK.get("DEFAULT_PERMISSION_CLASSES", [])
        assert isinstance(permission_classes, list), (
            "DEFAULT_PERMISSION_CLASSES must be a list"
        )
        assert len(permission_classes) >= 1, (
            "DEFAULT_PERMISSION_CLASSES must have at least one entry"
        )
