"""
Local development settings.

Inherits from base and enables debug mode, relaxed hosts, and SQLite by default.
Override DATABASE_URL in .env to use a local Postgres instance instead.
"""

from .base import *  # noqa: F401, F403

DEBUG = True

ALLOWED_HOSTS = ["*"]

# Dev convenience: accept any browser origin so the dashboard works regardless
# of which localhost port it runs on. Production relies on CORS_ALLOWED_ORIGINS.
CORS_ALLOW_ALL_ORIGINS = True  # noqa: F405

# Use Postgres when DATABASE_HOST is set (real Supabase), otherwise SQLite.
import os as _os
if _os.environ.get("DATABASE_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _os.environ["DATABASE_NAME"],
            "USER": _os.environ["DATABASE_USER"],
            "PASSWORD": _os.environ["DATABASE_PASSWORD"],
            "HOST": _os.environ["DATABASE_HOST"],
            "PORT": _os.environ.get("DATABASE_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }

# Django debug toolbar (optional — install separately)
# INSTALLED_APPS += ["debug_toolbar"]
# MIDDLEWARE = ["debug_toolbar.middleware.DebugToolbarMiddleware"] + MIDDLEWARE
