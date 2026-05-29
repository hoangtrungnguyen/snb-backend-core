"""Supabase PostgREST / Storage request headers — RLS-aware.

Two modes, mirroring the Supabase API key model
(https://supabase.com/docs/guides/getting-started/api-keys):

``user_headers(jwt)``
    Publishable key as ``apikey`` + the end user's JWT as the bearer token.
    PostgREST runs the request as the ``authenticated`` role with
    ``auth.uid()`` resolved to that user, so **Row Level Security policies are
    enforced**. Use for all user-facing endpoints.

``admin_headers()``
    Secret key as both ``apikey`` and bearer token. PostgREST runs as
    ``service_role``, which **bypasses RLS**. Use only for trusted
    backend/background work (cron jobs, system tasks).

Both default to ``Prefer: return=representation``; pass ``prefer=None`` to omit
the header, or another value (e.g. ``"return=minimal"``, ``"count=exact"``).
"""
from __future__ import annotations

from django.conf import settings


def supabase_url() -> str:
    """Base URL of the Supabase project REST/Storage API."""
    return settings.SUPABASE_URL


def _headers(apikey: str, bearer: str, prefer: str | None) -> dict:
    headers = {
        "apikey": apikey,
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if prefer is not None:
        headers["Prefer"] = prefer
    return headers


def user_headers(user_jwt: str, *, prefer: str | None = "return=representation") -> dict:
    """RLS-mode headers — publishable key + the end user's JWT."""
    return _headers(settings.SUPABASE_PUBLISHABLE_KEY, user_jwt, prefer)


def anon_headers(*, prefer: str | None = "return=representation") -> dict:
    """Anonymous-mode headers — publishable key as apikey and bearer.

    PostgREST runs as the ``anon`` role, so only policies granted to ``anon``
    apply (e.g. public reads of approved courts). Use for endpoints with no
    authenticated user.
    """
    return _headers(settings.SUPABASE_PUBLISHABLE_KEY, settings.SUPABASE_PUBLISHABLE_KEY, prefer)


def admin_headers(*, prefer: str | None = "return=representation") -> dict:
    """Bypass-RLS headers — secret key as apikey and bearer."""
    return _headers(settings.SUPABASE_SECRET_KEY, settings.SUPABASE_SECRET_KEY, prefer)
