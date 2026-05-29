"""
Tests that .env.example contains all required environment variable keys.

These tests parse the .env.example file and assert that every required key
is present, ensuring the file stays in sync with what the application needs.
"""

import os
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_env_example_keys(path: Path) -> set[str]:
    """Return the set of env-var keys defined in an .env.example file.

    Handles:
    - Normal assignments:  KEY=value
    - Commented-out examples:  # KEY=value  (we still count those as documented)
    - Blank lines and pure comment lines are ignored.
    """
    keys: set[str] = set()
    key_pattern = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=")
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            m = key_pattern.match(line)
            if m:
                keys.add(m.group(1))
    return keys


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def env_example_keys() -> set[str]:
    """Keys present in .env.example (repo root)."""
    repo_root = Path(__file__).resolve().parents[1]
    env_example = repo_root / ".env.example"
    assert env_example.exists(), f".env.example not found at {env_example}"
    return _parse_env_example_keys(env_example)


# ---------------------------------------------------------------------------
# Required keys (from grava-ea77.3.9 spec)
# ---------------------------------------------------------------------------

REQUIRED_KEYS = [
    # Django core
    "SECRET_KEY",
    "DJANGO_SETTINGS_MODULE",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    # Database
    "DATABASE_URL",
    # Supabase project
    "SUPABASE_URL",
    "SUPABASE_PUBLISHABLE_KEY",
    "SUPABASE_SECRET_KEY",
    # Supabase JWT / JWKS
    "SUPABASE_JWKS_URL",
    "SUPABASE_JWT_AUDIENCE",
    # Docker-compose local dev
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", REQUIRED_KEYS)
def test_required_key_present_in_env_example(key: str, env_example_keys: set[str]) -> None:
    """Each required environment variable must be documented in .env.example."""
    assert key in env_example_keys, (
        f"Required env var '{key}' is missing from .env.example. "
        "Add it (with a placeholder value) so developers know it is needed."
    )


def test_env_example_has_no_real_secrets() -> None:
    """Spot-check: .env.example must not contain obviously real secrets.

    We can't enumerate every possible secret, but we can reject patterns that
    look like real Supabase keys or long random strings in live values
    (i.e., not the placeholder strings we use).
    """
    repo_root = Path(__file__).resolve().parents[1]
    env_example = repo_root / ".env.example"
    content = env_example.read_text()

    # Real Supabase anon/service-role JWTs are long base64url strings.
    # Placeholders should contain angle-bracket tokens like <anon-key>.
    suspicious_jwt_pattern = re.compile(
        r"^(?!#)[A-Z_]+=eyJ[A-Za-z0-9_-]{20,}",
        re.MULTILINE,
    )
    matches = suspicious_jwt_pattern.findall(content)
    assert not matches, (
        f".env.example appears to contain a real JWT token. "
        f"Replace with a placeholder (e.g. <anon-key>). Matches: {matches}"
    )
