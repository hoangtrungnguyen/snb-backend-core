"""
pytest configuration — sets up Django before test collection.
"""
import os
import django


def pytest_configure(config):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")
    os.environ.setdefault("DJANGO_SECRET_KEY", "test-secret-key-for-pytest")
    os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
    os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
    django.setup()
