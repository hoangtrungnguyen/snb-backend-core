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
    """Configure Django settings for the test suite."""
    if not settings.configured:
        settings.configure(
            SECRET_KEY="test-secret-key-for-tests-only",
            DEBUG=True,
            ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"],
            DATABASES={
                "default": {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                }
            },
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "django.contrib.admin",
                "django.contrib.sessions",
                "django.contrib.messages",
            ],
            MIDDLEWARE=[
                "django.middleware.security.SecurityMiddleware",
                "django.contrib.sessions.middleware.SessionMiddleware",
                "django.middleware.common.CommonMiddleware",
                "django.contrib.auth.middleware.AuthenticationMiddleware",
                "django.contrib.messages.middleware.MessageMiddleware",
            ],
            TEMPLATES=[
                {
                    "BACKEND": "django.template.backends.django.DjangoTemplates",
                    "DIRS": [],
                    "APP_DIRS": True,
                    "OPTIONS": {
                        "context_processors": [
                            "django.template.context_processors.request",
                            "django.contrib.auth.context_processors.auth",
                            "django.contrib.messages.context_processors.messages",
                        ],
                    },
                }
            ],
            ROOT_URLCONF="spb_core.urls",
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            # Supabase JWKS URL — overridden per-test via mock
            SUPABASE_JWKS_URL="https://example.supabase.co/auth/v1/.well-known/jwks.json",
            SUPABASE_JWT_AUDIENCE="authenticated",
        )
        django.setup()
