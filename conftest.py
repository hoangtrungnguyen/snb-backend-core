"""
pytest configuration -- sets up Django before test collection.
"""
import os
import django
from dotenv import load_dotenv


def pytest_configure(config):
    # Load real Supabase credentials from .env if present
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path, override=False)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spb_core.settings.local")
    os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")

    # SUPABASE_URL / SUPABASE_KEY are optional — only set fallbacks for CI
    # where .env is absent. When .env omits them, leave them empty so the app
    # uses direct DB auth only.
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "")
    if supabase_url:
        os.environ.setdefault("SUPABASE_ANON_KEY", supabase_key)
        os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", supabase_key)
        os.environ.setdefault(
            "SUPABASE_JWKS_URL",
            f"{supabase_url}/auth/v1/.well-known/jwks.json",
        )
    django.setup()
