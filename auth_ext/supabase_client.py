"""Process-cached Supabase client factories.

Two clients: ``anon`` (publishable key) and ``admin`` (service-role key).
Both cached with ``lru_cache`` so we instantiate once per worker.

Tests should patch these factory functions (not the SDK internals) and call
``cache_clear`` in ``setUp`` to prevent leaking mocked clients between cases.
"""
from functools import lru_cache

from django.conf import settings
from supabase import Client, create_client


@lru_cache(maxsize=1)
def get_anon_client() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


@lru_cache(maxsize=1)
def get_admin_client() -> Client:
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
