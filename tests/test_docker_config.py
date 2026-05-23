"""Tests for Docker configuration files (grava-ea77.3.8).

Validates:
  - Dockerfile exists and uses Python 3.11+ slim base image
  - Dockerfile references gunicorn as WSGI server
  - Dockerfile binds to 0.0.0.0:8000
  - docker-compose.yml exists and defines web and db services
  - docker-compose.yml references .env file
  - .dockerignore exists and excludes required paths
  - .env.example exists with required variable names

No Docker daemon is required — tests inspect file content only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

# Repo root is two levels up from tests/
REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
ENV_EXAMPLE = REPO_ROOT / ".env.example"


# ---------------------------------------------------------------------------
# Dockerfile tests
# ---------------------------------------------------------------------------


class TestDockerfile:
    def test_dockerfile_exists(self):
        assert DOCKERFILE.exists(), "Dockerfile must exist at the repo root"

    def test_uses_python_311_slim(self):
        content = DOCKERFILE.read_text()
        # Matches python:3.11-slim, python:3.12-slim, python:3.11.x-slim, etc.
        import re
        pattern = r"FROM\s+python:3\.(1[1-9]|\d{2})[^\s]*slim"
        assert re.search(pattern, content), (
            "Dockerfile must use a Python 3.11+ slim base image (e.g. python:3.11-slim)"
        )

    def test_references_gunicorn(self):
        content = DOCKERFILE.read_text()
        assert "gunicorn" in content.lower(), (
            "Dockerfile must reference gunicorn as the WSGI server"
        )

    def test_binds_to_0000_8000(self):
        content = DOCKERFILE.read_text()
        assert "8000" in content, (
            "Dockerfile must expose/bind to port 8000"
        )

    def test_exposes_port_8000(self):
        content = DOCKERFILE.read_text()
        assert "EXPOSE 8000" in content or "EXPOSE\n8000" in content, (
            "Dockerfile must have EXPOSE 8000"
        )

    def test_copies_requirements(self):
        content = DOCKERFILE.read_text()
        assert "requirements.txt" in content, (
            "Dockerfile must copy requirements.txt"
        )

    def test_installs_dependencies(self):
        content = DOCKERFILE.read_text()
        assert "pip install" in content, (
            "Dockerfile must install dependencies via pip"
        )

    def test_gunicorn_bind_address(self):
        content = DOCKERFILE.read_text()
        assert "0.0.0.0:8000" in content, (
            "Dockerfile must configure gunicorn to bind to 0.0.0.0:8000"
        )

    def test_sets_workdir(self):
        content = DOCKERFILE.read_text()
        assert "WORKDIR" in content, (
            "Dockerfile must set a WORKDIR"
        )


# ---------------------------------------------------------------------------
# docker-compose.yml tests
# ---------------------------------------------------------------------------


class TestDockerCompose:
    def _load(self) -> dict:
        assert COMPOSE_FILE.exists(), "docker-compose.yml must exist at the repo root"
        with COMPOSE_FILE.open() as f:
            return yaml.safe_load(f)

    def test_compose_file_exists(self):
        assert COMPOSE_FILE.exists(), "docker-compose.yml must exist at the repo root"

    def test_web_service_defined(self):
        data = self._load()
        services = data.get("services", {})
        assert "web" in services, (
            "docker-compose.yml must define a 'web' service"
        )

    def test_db_service_defined(self):
        data = self._load()
        services = data.get("services", {})
        assert "db" in services, (
            "docker-compose.yml must define a 'db' service"
        )

    def test_db_uses_postgres(self):
        data = self._load()
        db = data["services"]["db"]
        image = db.get("image", "")
        assert "postgres" in image.lower(), (
            "The 'db' service must use a PostgreSQL image"
        )

    def test_web_depends_on_db(self):
        data = self._load()
        web = data["services"]["web"]
        depends = web.get("depends_on", [])
        # depends_on can be a list or dict (long form)
        if isinstance(depends, dict):
            assert "db" in depends, "web service must depend_on db"
        else:
            assert "db" in depends, "web service must depend_on db"

    def test_web_exposes_port_8000(self):
        data = self._load()
        web = data["services"]["web"]
        ports = web.get("ports", [])
        has_8000 = any("8000" in str(p) for p in ports)
        assert has_8000, (
            "web service must map port 8000"
        )

    def test_env_file_referenced(self):
        content = COMPOSE_FILE.read_text()
        assert ".env" in content, (
            "docker-compose.yml must reference a .env file for environment variables"
        )

    def test_web_service_has_build_or_image(self):
        data = self._load()
        web = data["services"]["web"]
        has_build = "build" in web
        has_image = "image" in web
        assert has_build or has_image, (
            "web service must specify 'build' or 'image'"
        )


# ---------------------------------------------------------------------------
# .dockerignore tests
# ---------------------------------------------------------------------------


class TestDockerIgnore:
    def test_dockerignore_exists(self):
        assert DOCKERIGNORE.exists(), ".dockerignore must exist at the repo root"

    def test_excludes_dot_git(self):
        content = DOCKERIGNORE.read_text()
        assert ".git" in content, ".dockerignore must exclude .git"

    def test_excludes_pycache(self):
        content = DOCKERIGNORE.read_text()
        assert "__pycache__" in content, ".dockerignore must exclude __pycache__"

    def test_excludes_pyc_files(self):
        content = DOCKERIGNORE.read_text()
        assert "*.pyc" in content, ".dockerignore must exclude *.pyc files"

    def test_excludes_env_file(self):
        content = DOCKERIGNORE.read_text()
        assert ".env" in content, ".dockerignore must exclude .env"

    def test_excludes_worktrees(self):
        content = DOCKERIGNORE.read_text()
        assert ".worktree" in content, ".dockerignore must exclude .worktree directories"


# ---------------------------------------------------------------------------
# .env.example tests
# ---------------------------------------------------------------------------


class TestEnvExample:
    def test_env_example_exists(self):
        assert ENV_EXAMPLE.exists(), ".env.example must exist at the repo root"

    def test_contains_django_secret_key(self):
        content = ENV_EXAMPLE.read_text()
        assert "SECRET_KEY" in content, ".env.example must include SECRET_KEY"

    def test_contains_database_url(self):
        content = ENV_EXAMPLE.read_text()
        assert "DATABASE_URL" in content, ".env.example must include DATABASE_URL"

    def test_contains_debug(self):
        content = ENV_EXAMPLE.read_text()
        assert "DEBUG" in content, ".env.example must include DEBUG"

    def test_contains_allowed_hosts(self):
        content = ENV_EXAMPLE.read_text()
        assert "ALLOWED_HOSTS" in content, ".env.example must include ALLOWED_HOSTS"
