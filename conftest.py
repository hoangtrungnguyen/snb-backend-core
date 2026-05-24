"""
pytest configuration — sets up Django before test collection.
"""
import os
import django


def pytest_configure(config):
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings")
    # Allow Django test client's default server name in tests
    os.environ.setdefault("DJANGO_ALLOWED_HOSTS", "localhost,testserver,127.0.0.1")
    django.setup()
