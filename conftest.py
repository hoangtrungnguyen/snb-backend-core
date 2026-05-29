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

    # Supabase API keys (new key model) are REQUIRED — settings refuses to start
    # without them. Provide dummy non-empty values for CI where .env is absent;
    # tests that exercise real Supabase calls override these via settings/mocks.
    if not os.environ.get("SUPABASE_PUBLISHABLE_KEY"):
        os.environ["SUPABASE_PUBLISHABLE_KEY"] = "sb_publishable_test"
    if not os.environ.get("SUPABASE_SECRET_KEY"):
        os.environ["SUPABASE_SECRET_KEY"] = "sb_secret_test"

    supabase_url = os.environ.get("SUPABASE_URL", "")
    if supabase_url:
        os.environ.setdefault(
            "SUPABASE_JWKS_URL",
            f"{supabase_url}/auth/v1/.well-known/jwks.json",
        )
    django.setup()
