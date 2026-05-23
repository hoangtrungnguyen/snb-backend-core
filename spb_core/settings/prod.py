"""
Production settings.

Inherits from base. Requires all secrets to be supplied via environment variables.
Never commit production secrets to version control.

Required env vars:
  SECRET_KEY    — Django secret key (no default; raises ImproperlyConfigured if missing)
  DATABASE_URL  — Full DB connection string, e.g. postgres://user:pass@host/dbname
  ALLOWED_HOSTS — Comma-separated list of allowed host names
"""

from .base import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

DEBUG = False

# ALLOWED_HOSTS must be set in env — no default, will raise if missing.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")  # noqa: F405

# ---------------------------------------------------------------------------
# HTTPS / cookie security headers
# ---------------------------------------------------------------------------

CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True

# ---------------------------------------------------------------------------
# Database — DATABASE_URL must be set in env for production.
# ---------------------------------------------------------------------------

DATABASES = {
    "default": env.db("DATABASE_URL")  # noqa: F405 — raises if DATABASE_URL is not set
}

# ---------------------------------------------------------------------------
# Static files (WhiteNoise or CDN)
# ---------------------------------------------------------------------------

# STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
