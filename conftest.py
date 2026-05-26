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

    # Fall back to dummy values only when .env is absent (CI without secrets)
    supabase_url = os.environ.get("SUPABASE_URL", "https://test.supabase.co")
    supabase_key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", "test-anon-key")
    os.environ.setdefault("SUPABASE_URL", supabase_url)
    os.environ.setdefault("SUPABASE_ANON_KEY", supabase_key)
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", supabase_key)
    os.environ.setdefault(
        "SUPABASE_JWKS_URL",
        f"{supabase_url}/auth/v1/.well-known/jwks.json",
    )
    django.setup()
