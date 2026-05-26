"""Core views for spb_core, including the health check endpoint."""
import logging
import os

import requests
from django.db import connections
from django.http import HttpResponse, JsonResponse
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


def _supabase_fetch(path, params=None):
    url = os.environ.get("SUPABASE_URL", "")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
           or os.environ.get("SUPABASE_ANON_KEY", "")
           or os.environ.get("SUPABASE_KEY", ""))
    if not url or not key:
        return None, "Supabase not configured"
    try:
        resp = requests.get(
            f"{url}/rest/v1/{path}",
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Prefer": "count=exact"},
            params=params or {},
            timeout=5,
        )
        if resp.status_code >= 400:
            return None, f"HTTP {resp.status_code}"
        count = resp.headers.get("content-range", "").split("/")[-1]
        return resp.json(), count
    except Exception as exc:
        return None, str(exc)


def _db_fetch(sql, cols):
    """Query directly via Django DB connection, bypassing REST API / RLS."""
    try:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute(sql)
            rows_data = [dict(zip(cols, row)) for row in cur.fetchall()]
            total = rows_data[0].get("_count") if rows_data else 0
        return rows_data, str(total or len(rows_data))
    except Exception as exc:
        logger.warning("Dashboard DB fetch failed: %s", exc)
        return None, "—"


@require_http_methods(["GET"])
def dashboard(request):
    users, users_count = _db_fetch(
        "SELECT id, email, role, created_at, COUNT(*) OVER() AS _count "
        "FROM public.users ORDER BY created_at DESC LIMIT 20",
        ["id", "email", "role", "created_at", "_count"],
    )
    courts, courts_count = _db_fetch(
        "SELECT id, name, slug, status, sport_types::text AS sport_type, created_at, COUNT(*) OVER() AS _count "
        "FROM public.courts ORDER BY created_at DESC LIMIT 20",
        ["id", "name", "slug", "status", "sport_type", "created_at", "_count"],
    )
    bookings, bookings_count = _db_fetch(
        "SELECT id, status, created_at, COUNT(*) OVER() AS _count "
        "FROM public.bookings ORDER BY created_at DESC LIMIT 5",
        ["id", "status", "created_at", "_count"],
    )

    def rows(data, cols):
        if data is None:
            return "<tr><td colspan='10' style='color:#888;padding:12px'>Unable to load — check database connection</td></tr>"
        if not data:
            return "<tr><td colspan='10' style='color:#aaa;padding:12px;font-style:italic'>No records yet</td></tr>"
        html = ""
        for row in data:
            html += "<tr>" + "".join(f"<td>{row.get(c,'')}</td>" for c in cols) + "</tr>"
        return html

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SNB Dashboard</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: system-ui, sans-serif; background: #f4f6f9; color: #222; }}
  header {{ background: #1a56db; color: #fff; padding: 16px 24px; display: flex; align-items: center; gap: 12px; }}
  header h1 {{ font-size: 1.2rem; font-weight: 600; }}
  .badge {{ background: #fff2; padding: 2px 8px; border-radius: 99px; font-size: 0.75rem; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; padding: 24px; }}
  .stat {{ background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 1px 3px #0001; }}
  .stat .num {{ font-size: 2rem; font-weight: 700; color: #1a56db; }}
  .stat .label {{ font-size: 0.85rem; color: #666; margin-top: 4px; }}
  .section {{ margin: 0 24px 24px; background: #fff; border-radius: 8px; box-shadow: 0 1px 3px #0001; overflow: hidden; }}
  .section h2 {{ font-size: 0.95rem; font-weight: 600; padding: 14px 18px; border-bottom: 1px solid #eee; background: #fafafa; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ text-align: left; padding: 10px 14px; background: #f8f9fb; color: #555; font-weight: 500; border-bottom: 1px solid #eee; }}
  td {{ padding: 9px 14px; border-bottom: 1px solid #f0f0f0; color: #333; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #f8fbff; }}
</style>
</head>
<body>
<header>
  <h1>SportBuddies Admin</h1>
  <span class="badge">local dev</span>
  <span class="badge" style="margin-left:auto">⚡ {'' if users is not None else '⚠ DB unreachable'}</span>
</header>

<div class="grid">
  <div class="stat"><div class="num">{users_count or '—'}</div><div class="label">Total Users</div></div>
  <div class="stat"><div class="num">{courts_count or '—'}</div><div class="label">Total Courts</div></div>
  <div class="stat"><div class="num">{bookings_count or '—'}</div><div class="label">Total Bookings</div></div>
</div>

<div class="section">
  <h2>Recent Users</h2>
  <table>
    <thead><tr><th>ID</th><th>Email</th><th>Role</th><th>Created</th></tr></thead>
    <tbody>{rows(users, ['id','email','role','created_at'])}</tbody>
  </table>
</div>

<div class="section">
  <h2>Recent Courts</h2>
  <table>
    <thead><tr><th>ID</th><th>Name</th><th>Slug</th><th>Status</th><th>Sport</th><th>Created</th></tr></thead>
    <tbody>{rows(courts, ['id','name','slug','status','sport_type','created_at'])}</tbody>
  </table>
</div>

<div class="section">
  <h2>Recent Bookings</h2>
  <table>
    <thead><tr><th>ID</th><th>Status</th><th>Created</th></tr></thead>
    <tbody>{rows(bookings, ['id','status','created_at'])}</tbody>
  </table>
</div>
</body>
</html>"""
    return HttpResponse(html)
