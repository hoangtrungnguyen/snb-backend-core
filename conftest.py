"""
pytest configuration -- sets up Django before test collection.
"""
import os
import django


def pytest_configure(config):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings.local")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
    os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
    os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
    os.environ.setdefault(
        "SUPABASE_JWKS_URL",
        "https://test.supabase.co/auth/v1/.well-known/jwks.json",
    )
    django.setup()
