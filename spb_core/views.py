"""Core views for spb_core, including the health check endpoint."""
import json
import logging

from django.db import connections
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

logger = logging.getLogger(__name__)


def _check_db() -> str:
    """Execute a simple SELECT 1 query to verify database connectivity.

    Returns "ok" on success, "error" on any failure.
    """
    try:
        conn = connections["default"]
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Health check: DB check failed: %s", exc)
        return "error"


def _check_realtime() -> str:
    """Stub check for Supabase Realtime.

    Supabase Realtime is an external service; always returns "ok" for now.
    """
    return "ok"


@require_http_methods(["GET"])
def health(request):
    """Health check endpoint.

    Returns JSON with db and realtime status.
    HTTP 200 when all checks pass; HTTP 503 when any check fails.
    No authentication required.
    """
    db_status = _check_db()
    realtime_status = _check_realtime()

    all_ok = db_status == "ok" and realtime_status == "ok"
    overall = "ok" if all_ok else "error"

    payload = {
        "status": overall,
        "db": db_status,
        "realtime": realtime_status,
    }
    http_status = 200 if all_ok else 503
    return JsonResponse(payload, status=http_status)
