"""
Pytest configuration for snb-backend-core.

Sets up minimal Django settings so Django apps can be imported in tests
without a full database or environment.
"""

import os
import sys

# Ensure the worktree root is on the path so local apps are importable.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import django
from django.conf import settings


def pytest_configure(config):
    if not settings.configured:
        settings.configure(
            SECRET_KEY="test-secret-key-not-for-production",
            DEBUG=True,
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
            ],
            # Supabase JWKS URL — overridden per-test via mock
            SUPABASE_JWKS_URL="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            SUPABASE_JWT_AUDIENCE="authenticated",
        )
        django.setup()
