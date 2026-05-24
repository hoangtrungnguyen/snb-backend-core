"""
Production settings.

Inherits from base. Requires all secrets to be supplied via environment variables.
Never commit production secrets to version control.

Required env vars:
  SECRET_KEY    — Django secret key (no default; raises ImproperlyConfigured if missing)
  DATABASE_URL  — Full DB connection string for Supabase connection pooler, e.g.
                  postgres://user:pass@aws-0-region.pooler.supabase.com:6543/postgres
  ALLOWED_HOSTS — Comma-separated list of allowed host names

Database notes:
  - Use the Supabase connection pooler (port 6543, PgBouncer transaction mode), NOT
    the direct port 5432.
  - CONN_MAX_AGE=60 keeps connections warm across requests, reducing handshake overhead
    while respecting PgBouncer's max_client_conn limits.
  - DISABLE_SERVER_SIDE_CURSORS=True is required because PgBouncer transaction-pooling
    mode does not support server-side cursors (PostgreSQL backend is not pinned per
    request, so the cursor state is lost between round-trips).
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
# Connects via Supabase PgBouncer connection pooler (port 6543, transaction mode).
# ---------------------------------------------------------------------------

_db_config = env.db("DATABASE_URL")  # noqa: F405 — raises ImproperlyConfigured if unset
_db_config["CONN_MAX_AGE"] = 60
_db_config["DISABLE_SERVER_SIDE_CURSORS"] = True

DATABASES = {
    "default": _db_config,
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
