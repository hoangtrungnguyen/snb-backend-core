"""
Base Django settings for snb-backend-core (SportBuddies).

All environment-specific settings files (local.py, prod.py) import from here
and override as needed.
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# django-environ — reads .env file if present (does NOT overwrite existing env vars)
env = environ.Env(
    DEBUG=(bool, False),
)
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file, overwrite=False)

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

# SECRET_KEY has no default — raises ImproperlyConfigured if missing from env.
SECRET_KEY = env("SECRET_KEY")

DEBUG = env("DEBUG")

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# ---------------------------------------------------------------------------
# Application definition
# ---------------------------------------------------------------------------

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
]

LOCAL_APPS = [
    "auth_ext",
    "players",
    "courts",
    "bookings",
    "series",
    "notifications",
    "analytics",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    # CorsMiddleware must run before CommonMiddleware (and any middleware that
    # may generate a response) so CORS headers land on every response,
    # including preflight (OPTIONS).
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "spb_core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "spb_core.wsgi.application"

# ---------------------------------------------------------------------------
# Database — built from individual vars or DATABASE_URL fallback.
# ---------------------------------------------------------------------------

_db_host = env.str("DATABASE_HOST", default="")
if _db_host:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env.str("DATABASE_NAME"),
            "USER": env.str("DATABASE_USER"),
            "PASSWORD": env.str("DATABASE_PASSWORD"),
            "HOST": _db_host,
            "PORT": env.str("DATABASE_PORT", default="5432"),
        }
    }
else:
    DATABASES = {
        "default": env.db(
            "DATABASE_URL",
            default="sqlite:///db.sqlite3",
        )
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Password validation
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static / media files
# ---------------------------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ---------------------------------------------------------------------------
# Django REST Framework defaults
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "auth_ext.authentication.SupabaseJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

# ---------------------------------------------------------------------------
# drf-spectacular (OpenAPI 3.0 schema)
# ---------------------------------------------------------------------------
# The live endpoints are plain django.views.View instances (manual auth +
# JsonResponse), which drf-spectacular cannot introspect. The schema is instead
# generated from a dedicated documentation URLConf of @api_view doc-stubs
# (see spb_core.api_docs) wired into SpectacularAPIView(urlconf=...) in urls.
# This keeps the real views untouched while still producing an accurate spec.
SPECTACULAR_SETTINGS = {
    "TITLE": "SportBuddies Core Engine API",
    "DESCRIPTION": (
        "Backend API for the SportBuddies platform (court owners + players). "
        "Optimized for developer integration and AI tool calling. "
        "All authenticated endpoints expect a Supabase-issued JWT as a Bearer token. "
        "Every error response carries a top-level `error` key (and often a `detail`)."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    # Supabase JWT Bearer scheme. Applied globally; public endpoints clear it
    # per-operation with @extend_schema(auth=[]).
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "BearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        }
    },
    "SECURITY": [{"BearerAuth": []}],
    # The health endpoint reuses an ok/error choice set across three fields;
    # name it once to avoid the "multiple names for the same choice set" warning.
    "ENUM_NAME_OVERRIDES": {
        "HealthStatusEnum": ["ok", "error"],
        "SlotStatusEnum": ["open", "booked", "blocked", "maintenance"],
        "BookingStatusEnum": ["pending", "confirmed", "cancelled", "completed"],
        "BookingStatusTransitionEnum": ["confirmed", "cancelled", "completed"],
        "JoinRequestStatusEnum": ["pending", "approved", "rejected"],
        "JoinStatusEnum": ["pending", "approved", "rejected", "none"],
        "AccessPolicyEnum": ["open", "private"],
    },
    "TAGS": [
        {"name": "Auth — Owner", "description": "Court owner authentication."},
        {"name": "Auth — Player", "description": "Player authentication & OAuth."},
        {"name": "Players", "description": "Authenticated player profile & devices."},
        {"name": "Courts", "description": "Court catalog, detail, settings & discovery."},
        {"name": "Slots", "description": "Availability slots, blocking, recurrence & play-together joins."},
        {"name": "Bookings", "description": "Single, manual & recurring bookings + pricing."},
        {"name": "System", "description": "Health & operational endpoints."},
    ],
}

# ---------------------------------------------------------------------------
# Supabase
# ---------------------------------------------------------------------------

SUPABASE_URL = env.str("SUPABASE_URL", default="")

# Supabase API keys — new key model.
# https://supabase.com/docs/guides/getting-started/api-keys
#   SUPABASE_PUBLISHABLE_KEY (sb_publishable_…) — user-facing requests; PostgREST
#                                                 enforces RLS as the bearer's JWT.
#   SUPABASE_SECRET_KEY      (sb_secret_…)      — backend/background jobs; bypasses RLS.
# Both are REQUIRED with no fallback. The app refuses to start if either is
# missing or empty.
SUPABASE_PUBLISHABLE_KEY = env.str("SUPABASE_PUBLISHABLE_KEY", default="")
SUPABASE_SECRET_KEY = env.str("SUPABASE_SECRET_KEY", default="")

_missing_supabase_keys = [
    name
    for name, value in (
        ("SUPABASE_PUBLISHABLE_KEY", SUPABASE_PUBLISHABLE_KEY),
        ("SUPABASE_SECRET_KEY", SUPABASE_SECRET_KEY),
    )
    if not value
]
if _missing_supabase_keys:
    raise ImproperlyConfigured(
        "Missing required Supabase API key(s): "
        + ", ".join(_missing_supabase_keys)
        + ". Set them in the environment (new key model: "
        "SUPABASE_PUBLISHABLE_KEY=sb_publishable_…, SUPABASE_SECRET_KEY=sb_secret_…)."
    )

_default_jwks = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else ""
SUPABASE_JWKS_URL = env.str("SUPABASE_JWKS_URL", default=_default_jwks)

# ---------------------------------------------------------------------------
# CORS (django-cors-headers)
# ---------------------------------------------------------------------------
# Cross-origin browser clients (e.g. the owner dashboard) need the backend to
# echo Access-Control-Allow-Origin on preflight + actual responses. Configure
# the allowed origins via the CORS_ALLOWED_ORIGINS env var (comma-separated);
# defaults cover the local dashboard dev ports.
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=[
        "http://127.0.0.1:8090",
        "http://localhost:8090",
    ],
)
CORS_ALLOW_CREDENTIALS = True
