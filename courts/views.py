"""
courts.views -- Court CRUD and Slot management API views.

Endpoints:
  POST   /api/courts/                    -- create court (owner only)
  GET    /api/courts/                    -- list courts (public, paginated, filterable)
  GET    /api/courts/{id}/               -- court detail (public)
  PATCH  /api/courts/{id}/               -- update court (owner only)
  DELETE /api/courts/{id}/               -- soft-delete: sets status=suspended (owner only)
  POST   /api/courts/slots               -- create slot (owner only) [grava-3106.2]
  PATCH  /api/courts/slots/{id}/block    -- block a slot (owner only) [grava-3106.3]
  PATCH  /api/courts/slots/{id}/unblock  -- unblock a slot (owner only) [grava-3106.3]
  POST   /api/courts/{id}/recurrence     -- recurring slot schedule generation [grava-3106.4]
  GET    /api/courts/by-slug/{slug}      -- court slug lookup (public) [grava-3106.6]

grava-3106.1 subtasks:
  grava-3106.1.1 -- POST /courts
  grava-3106.1.2 -- operating_hours schema: {mon: {open: "06:00", close: "22:00"}, ...}
  grava-3106.1.3 -- Geocoding: address -> (lat, lng) via Google Maps API
  grava-3106.1.4 -- Auto-slug: lowercased, hyphenated, unique suffix on collision
  grava-3106.1.5 -- GET /courts/{id}: public
  grava-3106.1.6 -- PATCH /courts/{id}: owner only, partial
  grava-3106.1.7 -- DELETE /courts/{id}: sets status=suspended; 409 on active bookings
  grava-3106.1.8 -- GET /courts: paginated; filters: owner_id, sport_type, status

grava-3106.2 subtasks:
  grava-3106.2.1 -- POST /slots: {court_id, start_at, end_at, status}
  grava-3106.2.2 -- Validates start_at/end_at within court operating_hours
  grava-3106.2.3 -- No overlapping slot for same court (409 Slot conflict)
  grava-3106.2.4 -- is_owner_slot: true -> status=blocked, skip payment

grava-3106.4 (BCORE-023 — OWNER-20 recurring slot schedule):
  POST /api/courts/{id}/recurrence
  Body: {days_of_week, start_time, end_time, from_date, until_date}
  Generates open-availability slots for each matching weekday in the date range.
  Overlapping slots and days outside operating_hours are silently skipped.
  Returns: {created, skipped, slots}

grava-3106.5 (BCORE-024 — Weekly schedule & slot detail queries):
  GET /api/courts/{id}/slots?from=DATE&to=DATE
      Returns all slots in [from, to) date range with status, booking_id, blocked_reason.
      Drives CAPP-041 and OWNER-18 weekly grid.
  GET /api/sports-centers/{id}/schedule?date=DATE
      Returns all courts for a sports center + their slots for that day.
      Used by CAPP-045 ScheduleGrid.
  GET /api/slots/{id}
      Slot detail: {id, court_id, court_name, start_at, end_at, duration_minutes,
                    status, access_policy, max_players, blocked_reason, booking_id, notes}.
      Used by OWNER-32 + booking-context lookups.

grava-3106.6 (BCORE-025 — Court slug lookup):
  GET /api/courts/by-slug/{slug}
      Public. Resolves a court slug to a full court detail response.
      Returns 404 if no court has that slug or status != approved.
      Slug matching is case-insensitive (slug lowercased before query).
      Used by the customer app deep-link router to resolve QR scans (screen 07).
"""
import json
import re
import unicodedata
from datetime import datetime, timezone, time as dt_time, date as dt_date, timedelta

import requests
from requests import RequestException as _RequestException
from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import AuthenticationFailed

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_DAYS = frozenset(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SLUG_NON_ALPHA = re.compile(r"[^a-z0-9]+")

_ACTIVE_BOOKING_STATUSES = ("pending", "confirmed")

# Maps Python weekday() (Mon=0, Sun=6) to operating_hours day keys
_WEEKDAY_TO_KEY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_VALID_SLOT_STATUSES = frozenset(["open", "booked", "blocked", "maintenance"])

_MAX_RECURRENCE_DAYS = 90  # maximum date range for POST /recurrence
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_slug(name: str) -> str:
    """
    Generate a URL-safe slug from *name*.

    Steps:
      1. Unicode normalize to ASCII (NFKD + encode to ascii ignoring errors).
      2. Lowercase.
      3. Replace non-alphanumeric runs with hyphens.
      4. Strip leading/trailing hyphens.
    """
    normalized = unicodedata.normalize("NFKD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    lower = ascii_name.lower()
    slug = _SLUG_NON_ALPHA.sub("-", lower).strip("-")
    return slug


def _validate_operating_hours(hours) -> None:
    """
    Validate *hours* against the schema:
      {<day>: {open: "HH:MM", close: "HH:MM"}, ...}

    Allowed days: mon, tue, wed, thu, fri, sat, sun.
    Times must match HH:MM (00:00 -- 23:59).

    Raises ValueError with a descriptive message on any violation.
    Accepts None (meaning: not set).
    """
    if hours is None:
        return
    if not isinstance(hours, dict):
        raise ValueError("operating_hours must be a dict or null.")
    for day, slot in hours.items():
        if day not in _VALID_DAYS:
            raise ValueError(
                f"Invalid day key \"{day}\". Must be one of: {sorted(_VALID_DAYS)}."
            )
        if not isinstance(slot, dict):
            raise ValueError(f"operating_hours[{day}] must be a dict.")
        for key in ("open", "close"):
            if key not in slot:
                raise ValueError(
                    f"operating_hours[{day}] is missing required key \"{key}\"."
                )
            val = slot[key]
            if not isinstance(val, str) or not _TIME_RE.match(val):
                raise ValueError(
                    f"operating_hours[{day}][{key}] = \"{val}\" is not a valid HH:MM time."
                )


def _get_supabase_keys():
    """Return (supabase_url, service_role_key) from settings."""
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
    service_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or anon_key
    return supabase_url, service_key


def _supabase_headers(key):
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }


def _authenticate_request(request):
    """
    Authenticate request via Supabase JWT.

    Returns (SupabaseUser, token) or None if no token.
    Raises AuthenticationFailed on invalid token.
    """
    from auth_ext.middleware import _decode_token
    from auth_ext.authentication import SupabaseUser

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[len("Bearer "):]
    if not token:
        return None

    payload = _decode_token(token)
    if payload is None:
        raise AuthenticationFailed("Invalid or expired token.")

    uid = payload.get("sub")
    if not uid:
        raise AuthenticationFailed("Token missing sub claim.")

    app_metadata = payload.get("app_metadata") or {}
    role = app_metadata.get("role") or "authenticated"
    return SupabaseUser(uid=uid, role=role), token


def _require_owner(request):
    """
    Authenticate + enforce owner role.

    Returns (user, None) on success, or (None, JsonResponse) on failure.
    """
    try:
        result = _authenticate_request(request)
    except AuthenticationFailed as exc:
        return None, JsonResponse({"error": str(exc.detail)}, status=401)

    if result is None:
        return None, JsonResponse(
            {"error": "Authentication credentials were not provided."}, status=401
        )

    user, _token = result
    if user.role != "owner":
        return None, JsonResponse(
            {"error": "You do not have permission to perform this action."}, status=403
        )
    return user, None


def _geocode_address(address: str):
    """
    Call Google Maps Geocoding API to convert *address* to (lat, lng).

    Returns (lat, lng) floats or (None, None) on failure / no results.
    Uses GOOGLE_MAPS_API_KEY from settings if available.
    """
    api_key = getattr(settings, "GOOGLE_MAPS_API_KEY", "")
    params = {"address": address}
    if api_key:
        params["key"] = api_key
    try:
        resp = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params=params,
            timeout=5,
        )
        data = resp.json()
        results = data.get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    except Exception:
        pass
    return None, None


def _build_unique_slug(base_slug: str, supabase_url: str, service_key: str) -> str:
    """
    Check Supabase for slug uniqueness and append a numeric suffix on collision.
    """
    courts_url = f"{supabase_url}/rest/v1/courts"
    candidate = base_slug
    suffix = 1
    while True:
        try:
            check = requests.get(
                courts_url,
                params={"slug": f"eq.{candidate}", "select": "id", "limit": "1"},
                headers=_supabase_headers(service_key),
                timeout=5,
            )
            rows = check.json() if check.status_code == 200 else []
        except Exception:
            rows = []

        if not rows:
            return candidate
        candidate = f"{base_slug}-{suffix}"
        suffix += 1


def _court_to_dict(row: dict) -> dict:
    """Serialize a Supabase court row to the API response shape."""
    return {
        "id": row.get("id"),
        "owner_id": row.get("owner_id"),
        "name": row.get("name"),
        "slug": row.get("slug"),
        "sport_types": row.get("sport_types", []),
        "capacity": row.get("capacity"),
        "price_per_hour": row.get("price_per_hour"),
        "operating_hours": row.get("operating_hours"),
        "address": row.get("address"),
        "lat": row.get("lat"),
        "lng": row.get("lng"),
        "status": row.get("status"),
        "amenities": row.get("amenities", []),
        "description": row.get("description"),
        "photos": row.get("photos", []),
        "auto_approve_single": row.get("auto_approve_single", False),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _slot_to_dict(row: dict) -> dict:
    """Serialize a Supabase slot row to the API response shape."""
    return {
        "id": row.get("id"),
        "court_id": row.get("court_id"),
        "start_at": row.get("start_at"),
        "end_at": row.get("end_at"),
        "status": row.get("status"),
        "is_owner_slot": row.get("is_owner_slot", False),
        "access_policy": row.get("access_policy"),
        "max_players": row.get("max_players"),
        "blocked_reason": row.get("blocked_reason"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _parse_iso_datetime(value: str) -> datetime | None:
    """
    Parse an ISO 8601 datetime string. Returns a timezone-aware datetime or None on failure.
    Accepts strings ending with 'Z' (UTC) or explicit UTC offsets.
    """
    if not isinstance(value, str):
        return None
    # Normalize 'Z' suffix to '+00:00' for Python 3.10 compatibility
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            # Treat naive datetime as UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _parse_hhmm(value: str) -> dt_time:
    """Parse 'HH:MM' string to a time object."""
    parts = value.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def _validate_slot_within_operating_hours(
    start_dt: datetime,
    end_dt: datetime,
    operating_hours: dict | None,
) -> str | None:
    """
    Check that *start_dt* and *end_dt* fall within the court's operating_hours.

    operating_hours format: {mon: {open: "HH:MM", close: "HH:MM"}, ...}

    Returns None if valid, or an error string if not.
    - If operating_hours is None/empty, the court operates 24/7 -> always valid.
    - Both timestamps must fall on the same day (no overnight slots crossing midnight).
    - The slot day must have an entry in operating_hours.
    - start_at.time() >= open AND end_at.time() <= close.
    """
    if not operating_hours:
        return None  # No restriction

    # Determine the weekday key for start_at (in UTC)
    day_key = _WEEKDAY_TO_KEY[start_dt.weekday()]

    day_hours = operating_hours.get(day_key)
    if day_hours is None:
        return (
            f"Court is closed on {day_key.capitalize()} "
            f"(no operating hours defined for that day)."
        )

    open_time = _parse_hhmm(day_hours["open"])
    close_time = _parse_hhmm(day_hours["close"])

    slot_start_time = start_dt.timetz().replace(tzinfo=None)
    slot_end_time = end_dt.timetz().replace(tzinfo=None)

    # Remove tz for comparison
    slot_start_time = dt_time(slot_start_time.hour, slot_start_time.minute)
    slot_end_time = dt_time(slot_end_time.hour, slot_end_time.minute)

    if slot_start_time < open_time:
        return (
            f"start_at ({slot_start_time.strftime('%H:%M')}) is before "
            f"court opening time ({open_time.strftime('%H:%M')}) on {day_key.capitalize()}."
        )
    if slot_end_time > close_time:
        return (
            f"end_at ({slot_end_time.strftime('%H:%M')}) is after "
            f"court closing time ({close_time.strftime('%H:%M')}) on {day_key.capitalize()}."
        )
    return None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class CourtsListView(View):
    """
    GET  /api/courts/ -- list courts (public, paginated, filterable)
    POST /api/courts/ -- create court (owner only)
    """

    def get(self, request):
        """List courts with optional filters: owner_id, sport_type, status."""
        supabase_url, service_key = _get_supabase_keys()
        courts_url = f"{supabase_url}/rest/v1/courts"

        params = {
            "select": "*",
            "order": "created_at.desc",
        }

        owner_id = request.GET.get("owner_id")
        if owner_id:
            params["owner_id"] = f"eq.{owner_id}"

        status = request.GET.get("status")
        if status:
            params["status"] = f"eq.{status}"

        sport_type = request.GET.get("sport_type")

        # Pagination
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 20))
        offset = (page - 1) * page_size
        params["limit"] = str(page_size)
        params["offset"] = str(offset)

        try:
            resp = requests.get(
                courts_url,
                params=params,
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if resp.status_code != 200:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        rows = resp.json()

        # Post-filter by sport_type (array contains)
        if sport_type:
            rows = [r for r in rows if sport_type in (r.get("sport_types") or [])]

        courts = [_court_to_dict(r) for r in rows]
        return JsonResponse({"results": courts, "page": page, "page_size": page_size},
                            status=200)

    def post(self, request):
        """Create a court. Owner role required."""
        user, err = _require_owner(request)
        if err is not None:
            return err

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        # Validate required fields
        name = body.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            return JsonResponse({"error": "name is required."}, status=400)

        # Validate operating_hours if provided
        operating_hours = body.get("operating_hours")
        try:
            _validate_operating_hours(operating_hours)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        supabase_url, service_key = _get_supabase_keys()

        # Generate slug
        base_slug = _generate_slug(name.strip())
        slug = _build_unique_slug(base_slug, supabase_url, service_key)

        # Geocode address
        address = body.get("address")
        lat, lng = None, None
        if address:
            lat, lng = _geocode_address(address)

        # Build insert payload
        insert_data = {
            "owner_id": user.id,
            "name": name.strip(),
            "slug": slug,
            "sport_types": body.get("sport_types", []),
            "capacity": body.get("capacity"),
            "price_per_hour": body.get("price_per_hour"),
            "operating_hours": operating_hours,
            "address": address,
            "lat": lat,
            "lng": lng,
            "amenities": body.get("amenities", []),
            "description": body.get("description"),
            "photos": body.get("photos", []),
            "status": "pending",
        }
        # Remove None values for cleaner insert
        insert_data = {k: v for k, v in insert_data.items() if v is not None or k in
                       ("operating_hours", "address", "capacity", "price_per_hour",
                        "description", "lat", "lng")}

        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            resp = requests.post(
                courts_url,
                json=insert_data,
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if resp.status_code not in (200, 201):
            return JsonResponse({"error": "Failed to create court."}, status=503)

        rows = resp.json()
        if not rows:
            return JsonResponse({"error": "Failed to create court."}, status=503)

        return JsonResponse(_court_to_dict(rows[0]), status=201)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class CourtDetailView(View):
    """
    GET    /api/courts/{id}/ -- public court detail
    PATCH  /api/courts/{id}/ -- update court (owner only)
    DELETE /api/courts/{id}/ -- soft-delete (owner only)
    """

    def _fetch_court(self, court_id: str, supabase_url: str, service_key: str):
        """Fetch a single court row by id. Returns dict or None."""
        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            resp = requests.get(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "*", "limit": "1"},
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return "error"
        if resp.status_code != 200:
            return "error"
        rows = resp.json()
        if not rows:
            return None
        return rows[0]

    def get(self, request, court_id):
        """Public endpoint -- no auth required."""
        supabase_url, service_key = _get_supabase_keys()
        court = self._fetch_court(court_id, supabase_url, service_key)
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)
        return JsonResponse(_court_to_dict(court), status=200)

    def patch(self, request, court_id):
        """Partial update. Owner role + ownership required."""
        user, err = _require_owner(request)
        if err is not None:
            return err

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        # Validate operating_hours if being updated
        if "operating_hours" in body:
            try:
                _validate_operating_hours(body["operating_hours"])
            except ValueError as exc:
                return JsonResponse({"error": str(exc)}, status=400)

        supabase_url, service_key = _get_supabase_keys()

        # Fetch court to check ownership
        court = self._fetch_court(court_id, supabase_url, service_key)
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)
        if court.get("owner_id") != user.id:
            return JsonResponse(
                {"error": "You do not have permission to modify this court."}, status=403
            )

        # Build update payload (allow only known updatable fields)
        updatable_fields = {
            "name", "sport_types", "capacity", "price_per_hour",
            "operating_hours", "address", "amenities", "description", "photos",
        }
        update_data = {k: v for k, v in body.items() if k in updatable_fields}
        if not update_data:
            return JsonResponse({"error": "No updatable fields provided."}, status=400)

        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            resp = requests.patch(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "*"},
                json=update_data,
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if resp.status_code != 200:
            return JsonResponse({"error": "Failed to update court."}, status=503)

        rows = resp.json()
        if not rows:
            return JsonResponse({"error": "Court not found."}, status=404)

        return JsonResponse(_court_to_dict(rows[0]), status=200)

    def delete(self, request, court_id):
        """
        Soft-delete: sets status=suspended.
        Returns 409 if there are active (pending/confirmed) bookings.
        """
        user, err = _require_owner(request)
        if err is not None:
            return err

        supabase_url, service_key = _get_supabase_keys()

        # Fetch court to check ownership
        court = self._fetch_court(court_id, supabase_url, service_key)
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)
        if court.get("owner_id") != user.id:
            return JsonResponse(
                {"error": "You do not have permission to delete this court."}, status=403
            )

        # Check for active bookings
        bookings_url = f"{supabase_url}/rest/v1/bookings"
        try:
            status_filter = ",".join(_ACTIVE_BOOKING_STATUSES)
            bookings_resp = requests.get(
                bookings_url,
                params={
                    "court_id": f"eq.{court_id}",
                    "status": f"in.({status_filter})",
                    "select": "id",
                    "limit": "1",
                },
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if bookings_resp.status_code == 200 and bookings_resp.json():
            return JsonResponse(
                {"error": "Cannot delete court with active bookings."}, status=409
            )

        # Soft-delete: set status=suspended
        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            resp = requests.patch(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "*"},
                json={"status": "suspended"},
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if resp.status_code != 200:
            return JsonResponse({"error": "Failed to suspend court."}, status=503)

        rows = resp.json()
        if not rows:
            return JsonResponse({"error": "Court not found."}, status=404)

        return JsonResponse(_court_to_dict(rows[0]), status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class SlotsView(View):
    """
    POST /api/courts/slots -- create a slot (owner only).

    Request body:
      {
        "court_id": "<uuid>",
        "start_at": "<ISO 8601 datetime>",
        "end_at":   "<ISO 8601 datetime>",
        "status":   "open" | "booked" | "blocked" | "maintenance",   # optional
        "is_owner_slot": true | false                                  # optional
      }

    Validations (grava-3106.2.2, grava-3106.2.3, grava-3106.2.4):
      - start_at and end_at must fall within court's operating_hours.
      - No overlapping slot may exist for the same court (409 Slot conflict).
      - is_owner_slot=true forces status=blocked (payment flow skipped).
    """

    def post(self, request):
        # --- Auth ---
        user, err = _require_owner(request)
        if err is not None:
            return err

        # --- Parse body ---
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        # --- Required fields ---
        court_id = body.get("court_id")
        if not court_id or not isinstance(court_id, str) or not court_id.strip():
            return JsonResponse({"error": "court_id is required."}, status=400)
        court_id = court_id.strip()

        start_at_raw = body.get("start_at")
        end_at_raw = body.get("end_at")

        if not start_at_raw:
            return JsonResponse({"error": "start_at is required."}, status=400)
        if not end_at_raw:
            return JsonResponse({"error": "end_at is required."}, status=400)

        start_dt = _parse_iso_datetime(start_at_raw)
        if start_dt is None:
            return JsonResponse(
                {"error": "start_at must be a valid ISO 8601 datetime."}, status=400
            )

        end_dt = _parse_iso_datetime(end_at_raw)
        if end_dt is None:
            return JsonResponse(
                {"error": "end_at must be a valid ISO 8601 datetime."}, status=400
            )

        if end_dt <= start_dt:
            return JsonResponse(
                {"error": "end_at must be after start_at."}, status=400
            )

        # --- Optional fields ---
        is_owner_slot = body.get("is_owner_slot", False)
        if not isinstance(is_owner_slot, bool):
            return JsonResponse(
                {"error": "is_owner_slot must be a boolean."}, status=400
            )

        # grava-3106.2.4: owner slot -> force status=blocked
        if is_owner_slot:
            status = "blocked"
        else:
            status = body.get("status", "open")
            if status not in _VALID_SLOT_STATUSES:
                return JsonResponse(
                    {
                        "error": (
                            f"status must be one of: "
                            f"{', '.join(sorted(_VALID_SLOT_STATUSES))}."
                        )
                    },
                    status=400,
                )

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # --- Fetch court (to verify existence and operating_hours) ---
        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            court_resp = requests.get(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "id,owner_id,operating_hours", "limit": "1"},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if court_resp.status_code != 200:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        court_rows = court_resp.json()
        if not court_rows:
            return JsonResponse({"error": "Court not found."}, status=404)

        court = court_rows[0]

        # grava-3106.2.2: validate start_at/end_at within operating_hours
        operating_hours = court.get("operating_hours")
        hours_error = _validate_slot_within_operating_hours(start_dt, end_dt, operating_hours)
        if hours_error:
            return JsonResponse({"error": hours_error}, status=400)

        # grava-3106.2.3: check for overlapping slots on the same court
        # Overlap condition: existing.start_at < new.end_at AND existing.end_at > new.start_at
        slots_url = f"{supabase_url}/rest/v1/slots"
        start_iso = start_dt.isoformat()
        end_iso = end_dt.isoformat()
        try:
            overlap_resp = requests.get(
                slots_url,
                params={
                    "court_id": f"eq.{court_id}",
                    "start_at": f"lt.{end_iso}",
                    "end_at": f"gt.{start_iso}",
                    "select": "id",
                    "limit": "1",
                },
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if overlap_resp.status_code != 200:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if overlap_resp.json():
            return JsonResponse(
                {"error": "Slot conflict: an overlapping slot already exists for this court."},
                status=409,
            )

        # --- Insert slot ---
        insert_data = {
            "court_id": court_id,
            "start_at": start_iso,
            "end_at": end_iso,
            "status": status,
            "is_owner_slot": is_owner_slot,
        }

        try:
            create_resp = requests.post(
                slots_url,
                json=insert_data,
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if create_resp.status_code not in (200, 201):
            return JsonResponse({"error": "Failed to create slot."}, status=503)

        rows = create_resp.json()
        if not rows:
            return JsonResponse({"error": "Failed to create slot."}, status=503)

        return JsonResponse(_slot_to_dict(rows[0]), status=201)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class SlotBlockView(View):
    """
    PATCH /api/courts/slots/{id}/block -- block a slot (owner only).

    Sets status=blocked and optionally stores blocked_reason.
    Returns 409 if the slot is currently booked (cannot block a booked slot).

    grava-3106.3.1
    """

    def _fetch_slot(self, slot_id: str, supabase_url: str, service_key: str):
        """Fetch a single slot row by id. Returns dict, None, or 'error'."""
        slots_url = f"{supabase_url}/rest/v1/slots"
        try:
            resp = requests.get(
                slots_url,
                params={"id": f"eq.{slot_id}", "select": "*", "limit": "1"},
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return "error"
        if resp.status_code != 200:
            return "error"
        rows = resp.json()
        if not rows:
            return None
        return rows[0]

    def _fetch_court(self, court_id: str, supabase_url: str, service_key: str):
        """Fetch a single court row by id. Returns dict, None, or 'error'."""
        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            resp = requests.get(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "id,owner_id", "limit": "1"},
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return "error"
        if resp.status_code != 200:
            return "error"
        rows = resp.json()
        if not rows:
            return None
        return rows[0]

    def patch(self, request, slot_id):
        # --- Auth ---
        user, err = _require_owner(request)
        if err is not None:
            return err

        supabase_url, service_key = _get_supabase_keys()

        # --- Fetch slot ---
        slot = self._fetch_slot(slot_id, supabase_url, service_key)
        if slot == "error":
            return JsonResponse({"error": "Slot service unavailable."}, status=503)
        if slot is None:
            return JsonResponse({"error": "Slot not found."}, status=404)

        # --- Ownership: fetch the court and verify owner ---
        court = self._fetch_court(slot["court_id"], supabase_url, service_key)
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None or court.get("owner_id") != user.id:
            return JsonResponse(
                {"error": "You do not have permission to modify this slot."}, status=403
            )

        # --- 409 if slot is currently booked ---
        if slot.get("status") == "booked":
            return JsonResponse(
                {"error": "Cannot block a slot that has an active booking."},
                status=409,
            )

        # --- Parse optional blocked_reason from body ---
        blocked_reason = None
        try:
            body = json.loads(request.body) if request.body else {}
        except (json.JSONDecodeError, ValueError):
            body = {}
        if isinstance(body, dict):
            blocked_reason = body.get("blocked_reason")

        # --- Patch slot in Supabase ---
        update_data = {"status": "blocked", "blocked_reason": blocked_reason}
        slots_url = f"{supabase_url}/rest/v1/slots"
        try:
            patch_resp = requests.patch(
                slots_url,
                params={"id": f"eq.{slot_id}", "select": "*"},
                json=update_data,
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if patch_resp.status_code != 200:
            return JsonResponse({"error": "Failed to update slot."}, status=503)

        rows = patch_resp.json()
        if not rows:
            return JsonResponse({"error": "Slot not found."}, status=404)

        return JsonResponse(_slot_to_dict(rows[0]), status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class SlotUnblockView(View):
    """
    PATCH /api/courts/slots/{id}/unblock -- unblock a slot (owner only).

    Sets status=open and clears blocked_reason.
    Slot immediately re-appears in the player slot picker.

    grava-3106.3.2
    """

    def _fetch_slot(self, slot_id: str, supabase_url: str, service_key: str):
        """Fetch a single slot row by id. Returns dict, None, or 'error'."""
        slots_url = f"{supabase_url}/rest/v1/slots"
        try:
            resp = requests.get(
                slots_url,
                params={"id": f"eq.{slot_id}", "select": "*", "limit": "1"},
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return "error"
        if resp.status_code != 200:
            return "error"
        rows = resp.json()
        if not rows:
            return None
        return rows[0]

    def _fetch_court(self, court_id: str, supabase_url: str, service_key: str):
        """Fetch a single court row by id. Returns dict, None, or 'error'."""
        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            resp = requests.get(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "id,owner_id", "limit": "1"},
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return "error"
        if resp.status_code != 200:
            return "error"
        rows = resp.json()
        if not rows:
            return None
        return rows[0]

    def patch(self, request, slot_id):
        # --- Auth ---
        user, err = _require_owner(request)
        if err is not None:
            return err

        supabase_url, service_key = _get_supabase_keys()

        # --- Fetch slot ---
        slot = self._fetch_slot(slot_id, supabase_url, service_key)
        if slot == "error":
            return JsonResponse({"error": "Slot service unavailable."}, status=503)
        if slot is None:
            return JsonResponse({"error": "Slot not found."}, status=404)

        # --- Ownership: fetch the court and verify owner ---
        court = self._fetch_court(slot["court_id"], supabase_url, service_key)
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None or court.get("owner_id") != user.id:
            return JsonResponse(
                {"error": "You do not have permission to modify this slot."}, status=403
            )

        # --- Patch slot in Supabase: set status=open, clear blocked_reason ---
        update_data = {"status": "open", "blocked_reason": None}
        slots_url = f"{supabase_url}/rest/v1/slots"
        try:
            patch_resp = requests.patch(
                slots_url,
                params={"id": f"eq.{slot_id}", "select": "*"},
                json=update_data,
                headers=_supabase_headers(service_key),
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if patch_resp.status_code != 200:
            return JsonResponse({"error": "Failed to update slot."}, status=503)

        rows = patch_resp.json()
        if not rows:
            return JsonResponse({"error": "Slot not found."}, status=404)

        return JsonResponse(_slot_to_dict(rows[0]), status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


def _parse_date(value: str) -> dt_date | None:
    """Parse a YYYY-MM-DD string. Returns a date object or None on failure."""
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return None
    try:
        return dt_date.fromisoformat(value)
    except ValueError:
        return None


@method_decorator(csrf_exempt, name="dispatch")
class RecurrenceView(View):
    """
    POST /api/courts/{court_id}/recurrence

    Generate open-availability slots on a recurring weekly schedule (grava-3106.4).

    BCORE-023 / OWNER-20. Distinct from BCORE-036 which generates *bookings*
    against existing slots — this generates *slots* (open availability).

    Request body:
      {
        "days_of_week": ["mon", "wed", "fri"],   # which weekdays to create slots on
        "start_time":   "09:00",                  # slot start time HH:MM (UTC)
        "end_time":     "11:00",                  # slot end time HH:MM (UTC)
        "from_date":    "2026-06-01",             # first day of recurrence YYYY-MM-DD
        "until_date":   "2026-06-30"              # last day (inclusive) YYYY-MM-DD
      }

    Response 200:
      {
        "created": <int>,    # number of slots successfully inserted
        "skipped": <int>,    # occurrences skipped (overlap or outside operating_hours)
        "slots":   [...]     # array of created slot objects
      }

    Constraints:
      - Owner auth required; court must belong to the authenticated owner.
      - until_date - from_date must be <= 90 days.
      - Overlapping slots are silently skipped (counted in skipped).
      - Days outside court operating_hours are silently skipped (counted in skipped).
    """

    def post(self, request, court_id):
        # --- Auth ---
        user, err = _require_owner(request)
        if err is not None:
            return err

        # --- Parse body ---
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        # --- Validate days_of_week ---
        days_of_week = body.get("days_of_week")
        if days_of_week is None:
            return JsonResponse({"error": "days_of_week is required."}, status=400)
        if not isinstance(days_of_week, list) or not days_of_week:
            return JsonResponse(
                {"error": "days_of_week must be a non-empty list."}, status=400
            )
        for day in days_of_week:
            if day not in _VALID_DAYS:
                return JsonResponse(
                    {
                        "error": (
                            f"Invalid day \"{day}\" in days_of_week. "
                            f"Must be one of: {sorted(_VALID_DAYS)}."
                        )
                    },
                    status=400,
                )

        # --- Validate start_time / end_time ---
        start_time_raw = body.get("start_time")
        if not start_time_raw:
            return JsonResponse({"error": "start_time is required."}, status=400)
        if not isinstance(start_time_raw, str) or not _TIME_RE.match(start_time_raw):
            return JsonResponse(
                {"error": f"start_time \"{start_time_raw}\" is not a valid HH:MM time."},
                status=400,
            )

        end_time_raw = body.get("end_time")
        if not end_time_raw:
            return JsonResponse({"error": "end_time is required."}, status=400)
        if not isinstance(end_time_raw, str) or not _TIME_RE.match(end_time_raw):
            return JsonResponse(
                {"error": f"end_time \"{end_time_raw}\" is not a valid HH:MM time."},
                status=400,
            )

        slot_start_time = _parse_hhmm(start_time_raw)
        slot_end_time = _parse_hhmm(end_time_raw)
        if slot_end_time <= slot_start_time:
            return JsonResponse(
                {"error": "end_time must be after start_time."}, status=400
            )

        # --- Validate from_date / until_date ---
        from_date_raw = body.get("from_date")
        if not from_date_raw:
            return JsonResponse({"error": "from_date is required."}, status=400)
        from_date = _parse_date(from_date_raw)
        if from_date is None:
            return JsonResponse(
                {"error": f"from_date \"{from_date_raw}\" is not a valid YYYY-MM-DD date."},
                status=400,
            )

        until_date_raw = body.get("until_date")
        if not until_date_raw:
            return JsonResponse({"error": "until_date is required."}, status=400)
        until_date = _parse_date(until_date_raw)
        if until_date is None:
            return JsonResponse(
                {"error": f"until_date \"{until_date_raw}\" is not a valid YYYY-MM-DD date."},
                status=400,
            )

        if until_date < from_date:
            return JsonResponse(
                {"error": "until_date must be on or after from_date."}, status=400
            )

        # --- Enforce 90-day maximum ---
        if (until_date - from_date).days > _MAX_RECURRENCE_DAYS:
            return JsonResponse(
                {
                    "error": (
                        f"Date range must not exceed {_MAX_RECURRENCE_DAYS} days "
                        f"(got {(until_date - from_date).days} days)."
                    )
                },
                status=400,
            )

        # --- Fetch court (verify existence + ownership + operating_hours) ---
        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)
        courts_url = f"{supabase_url}/rest/v1/courts"
        slots_url = f"{supabase_url}/rest/v1/slots"

        try:
            court_resp = requests.get(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "id,owner_id,operating_hours", "limit": "1"},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if court_resp.status_code != 200:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        court_rows = court_resp.json()
        if not court_rows:
            return JsonResponse({"error": "Court not found."}, status=404)

        court = court_rows[0]
        if court.get("owner_id") != user.id:
            return JsonResponse(
                {"error": "You do not have permission to modify this court."}, status=403
            )

        operating_hours = court.get("operating_hours")
        days_of_week_set = set(days_of_week)

        # --- Generate occurrences ---
        created_slots = []
        skipped = 0

        current = from_date
        while current <= until_date:
            day_key = _WEEKDAY_TO_KEY[current.weekday()]

            if day_key not in days_of_week_set:
                current += timedelta(days=1)
                continue

            # Build UTC datetimes for this occurrence
            start_dt = datetime(
                current.year, current.month, current.day,
                slot_start_time.hour, slot_start_time.minute,
                tzinfo=timezone.utc,
            )
            end_dt = datetime(
                current.year, current.month, current.day,
                slot_end_time.hour, slot_end_time.minute,
                tzinfo=timezone.utc,
            )

            # Check operating_hours — skip if outside
            hours_error = _validate_slot_within_operating_hours(start_dt, end_dt, operating_hours)
            if hours_error:
                skipped += 1
                current += timedelta(days=1)
                continue

            # Check for overlapping slots
            start_iso = start_dt.isoformat()
            end_iso = end_dt.isoformat()
            try:
                overlap_resp = requests.get(
                    slots_url,
                    params={
                        "court_id": f"eq.{court_id}",
                        "start_at": f"lt.{end_iso}",
                        "end_at": f"gt.{start_iso}",
                        "select": "id",
                        "limit": "1",
                    },
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                skipped += 1
                current += timedelta(days=1)
                continue

            if overlap_resp.status_code != 200 or overlap_resp.json():
                skipped += 1
                current += timedelta(days=1)
                continue

            # Insert the slot
            insert_data = {
                "court_id": court_id,
                "start_at": start_iso,
                "end_at": end_iso,
                "status": "open",
                "is_owner_slot": False,
            }
            try:
                create_resp = requests.post(
                    slots_url,
                    json=insert_data,
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                skipped += 1
                current += timedelta(days=1)
                continue

            if create_resp.status_code not in (200, 201):
                skipped += 1
                current += timedelta(days=1)
                continue

            rows = create_resp.json()
            if rows:
                created_slots.append(_slot_to_dict(rows[0]))
            else:
                skipped += 1

            current += timedelta(days=1)

        return JsonResponse(
            {
                "created": len(created_slots),
                "skipped": skipped,
                "slots": created_slots,
            },
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# grava-3106.5 helpers
# ---------------------------------------------------------------------------

def _slot_to_detail_dict(slot_row: dict, court_name: str | None = None) -> dict:
    """
    Serialize a slot row to the full slot-detail shape (grava-3106.5.3).

    Includes all fields required by OWNER-32 + booking-context lookups:
      id, court_id, court_name, start_at, end_at, duration_minutes,
      status, access_policy, max_players, blocked_reason, booking_id, notes.
    """
    start_at = slot_row.get("start_at")
    end_at = slot_row.get("end_at")

    # Compute duration_minutes from ISO timestamps when available.
    duration_minutes = None
    if start_at and end_at:
        start_dt = _parse_iso_datetime(start_at)
        end_dt = _parse_iso_datetime(end_at)
        if start_dt and end_dt:
            duration_minutes = int((end_dt - start_dt).total_seconds() // 60)

    return {
        "id": slot_row.get("id"),
        "court_id": slot_row.get("court_id"),
        "court_name": court_name,
        "start_at": start_at,
        "end_at": end_at,
        "duration_minutes": duration_minutes,
        "status": slot_row.get("status"),
        "access_policy": slot_row.get("access_policy"),
        "max_players": slot_row.get("max_players"),
        "blocked_reason": slot_row.get("blocked_reason"),
        "booking_id": slot_row.get("booking_id"),
        "notes": slot_row.get("notes"),
    }


def _slot_to_range_dict(slot_row: dict) -> dict:
    """
    Serialize a slot row for the weekly-range / schedule views (grava-3106.5.1/5.2).

    Includes status, booking_id, blocked_reason per grava-3106.5.4.
    """
    return {
        "id": slot_row.get("id"),
        "court_id": slot_row.get("court_id"),
        "start_at": slot_row.get("start_at"),
        "end_at": slot_row.get("end_at"),
        "status": slot_row.get("status"),
        "is_owner_slot": slot_row.get("is_owner_slot", False),
        "access_policy": slot_row.get("access_policy"),
        "max_players": slot_row.get("max_players"),
        "blocked_reason": slot_row.get("blocked_reason"),
        "booking_id": slot_row.get("booking_id"),
        "notes": slot_row.get("notes"),
    }


# ---------------------------------------------------------------------------
# grava-3106.5.1  GET /api/courts/{id}/slots?from=DATE&to=DATE
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class CourtSlotsRangeView(View):
    """
    GET /api/courts/{id}/slots?from=DATE&to=DATE

    Returns all slots for the given court within the date range.
    Public endpoint — no auth required.

    Query parameters:
      from  YYYY-MM-DD  (required) — inclusive start date
      to    YYYY-MM-DD  (required) — end date boundary

    Response 200:
      {"results": [<slot>, ...]}

    Each slot includes: id, court_id, start_at, end_at, status, booking_id,
    blocked_reason (grava-3106.5.4).
    """

    def get(self, request, court_id):
        from_raw = request.GET.get("from")
        to_raw = request.GET.get("to")

        # --- Validate params ---
        if not from_raw:
            return JsonResponse({"error": "from query parameter is required."}, status=400)
        if not to_raw:
            return JsonResponse({"error": "to query parameter is required."}, status=400)

        from_date = _parse_date(from_raw)
        if from_date is None:
            return JsonResponse(
                {"error": f"from \"{from_raw}\" is not a valid YYYY-MM-DD date."}, status=400
            )
        to_date = _parse_date(to_raw)
        if to_date is None:
            return JsonResponse(
                {"error": f"to \"{to_raw}\" is not a valid YYYY-MM-DD date."}, status=400
            )

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # --- Fetch court (verify existence) ---
        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            court_resp = requests.get(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "id", "limit": "1"},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if court_resp.status_code != 200:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if not court_resp.json():
            return JsonResponse({"error": "Court not found."}, status=404)

        # --- Fetch slots in range ---
        # Range: start_at >= from_date (midnight UTC) AND start_at < to_date (next midnight)
        from_iso = f"{from_date.isoformat()}T00:00:00+00:00"
        to_iso = f"{to_date.isoformat()}T00:00:00+00:00"

        slots_url = f"{supabase_url}/rest/v1/slots"
        try:
            slots_resp = requests.get(
                slots_url,
                params={
                    "court_id": f"eq.{court_id}",
                    "start_at": f"gte.{from_iso}",
                    "end_at": f"lte.{to_iso}",
                    "select": "*",
                    "order": "start_at.asc",
                },
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if slots_resp.status_code != 200:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        rows = slots_resp.json()
        return JsonResponse(
            {"results": [_slot_to_range_dict(r) for r in rows]},
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# grava-3106.5.2  GET /api/sports-centers/{id}/schedule?date=DATE
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class SportsCenterScheduleView(View):
    """
    GET /api/sports-centers/{id}/schedule?date=DATE

    Returns all courts belonging to the sports center plus their slots for the given day.
    Public endpoint — no auth required.

    Query parameters:
      date  YYYY-MM-DD  (required)

    Response 200:
      {
        "date": "2026-05-25",
        "courts": [
          {
            "id": "...",
            "name": "Court Alpha",
            "status": "active",
            ...
            "slots": [<slot>, ...]
          },
          ...
        ]
      }

    Each slot includes booking_id and blocked_reason per grava-3106.5.4.
    """

    def get(self, request, sc_id):
        date_raw = request.GET.get("date")
        if not date_raw:
            return JsonResponse({"error": "date query parameter is required."}, status=400)

        target_date = _parse_date(date_raw)
        if target_date is None:
            return JsonResponse(
                {"error": f"date \"{date_raw}\" is not a valid YYYY-MM-DD date."}, status=400
            )

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # --- Fetch all courts for this sports center ---
        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            courts_resp = requests.get(
                courts_url,
                params={
                    "sports_center_id": f"eq.{sc_id}",
                    "select": "*",
                    "order": "name.asc",
                },
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if courts_resp.status_code != 200:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        court_rows = courts_resp.json()

        # --- For each court, fetch slots for the given day ---
        from_iso = f"{target_date.isoformat()}T00:00:00+00:00"
        next_day = target_date + timedelta(days=1)
        to_iso = f"{next_day.isoformat()}T00:00:00+00:00"

        slots_url = f"{supabase_url}/rest/v1/slots"
        courts_with_slots = []
        for court in court_rows:
            court_id = court.get("id")
            try:
                slots_resp = requests.get(
                    slots_url,
                    params={
                        "court_id": f"eq.{court_id}",
                        "start_at": f"gte.{from_iso}",
                        "end_at": f"lte.{to_iso}",
                        "select": "*",
                        "order": "start_at.asc",
                    },
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                return JsonResponse({"error": "Slot service unavailable."}, status=503)

            if slots_resp.status_code != 200:
                return JsonResponse({"error": "Slot service unavailable."}, status=503)

            slot_rows = slots_resp.json()
            court_data = _court_to_dict(court)
            court_data["slots"] = [_slot_to_range_dict(s) for s in slot_rows]
            courts_with_slots.append(court_data)

        return JsonResponse(
            {"date": target_date.isoformat(), "courts": courts_with_slots},
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# grava-3106.5.3  GET /api/slots/{id}
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class SlotDetailView(View):
    """
    GET /api/slots/{id}

    Returns full slot detail including court_name and computed duration_minutes.
    Public endpoint — no auth required.

    Response 200:
      {
        "id": "...",
        "court_id": "...",
        "court_name": "Court Alpha",
        "start_at": "...",
        "end_at": "...",
        "duration_minutes": 120,
        "status": "open" | "booked" | "blocked" | "maintenance",
        "access_policy": "...",
        "max_players": ...,
        "blocked_reason": null | "...",
        "booking_id": null | "...",
        "notes": null | "..."
      }

    grava-3106.5.3 / grava-3106.5.4
    """

    def get(self, request, slot_id):
        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # --- Fetch slot ---
        slots_url = f"{supabase_url}/rest/v1/slots"
        try:
            slot_resp = requests.get(
                slots_url,
                params={"id": f"eq.{slot_id}", "select": "*", "limit": "1"},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if slot_resp.status_code != 200:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        slot_rows = slot_resp.json()
        if not slot_rows:
            return JsonResponse({"error": "Slot not found."}, status=404)

        slot = slot_rows[0]

        # --- Fetch related court to get court_name ---
        court_id = slot.get("court_id")
        court_name = None
        if court_id:
            courts_url = f"{supabase_url}/rest/v1/courts"
            try:
                court_resp = requests.get(
                    courts_url,
                    params={"id": f"eq.{court_id}", "select": "id,name", "limit": "1"},
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                return JsonResponse({"error": "Court service unavailable."}, status=503)

            if court_resp.status_code != 200:
                return JsonResponse({"error": "Court service unavailable."}, status=503)

            court_rows = court_resp.json()
            if court_rows:
                court_name = court_rows[0].get("name")

        return JsonResponse(_slot_to_detail_dict(slot, court_name=court_name), status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# grava-3106.6  GET /api/courts/by-slug/{slug}
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class CourtSlugLookupView(View):
    """
    GET /api/courts/by-slug/{slug}

    Public endpoint. Resolves a court slug to a full court detail response.

    Returns 404 if:
      - No court has that slug.
      - The matched court's status is not "approved".

    Slug matching is case-insensitive (slug is lowercased before querying
    Supabase via `ilike` / exact match on `lower(courts.slug)`).

    Used by the customer app's deep-link router to resolve QR-code scans to
    the court detail screen (CAPP screen 07).

    grava-3106.6.1, grava-3106.6.2, grava-3106.6.3
    """

    def get(self, request, slug: str):
        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # grava-3106.6.2 — case-insensitive: lowercase the slug before querying
        normalized_slug = slug.lower()

        courts_url = f"{supabase_url}/rest/v1/courts"
        try:
            resp = requests.get(
                courts_url,
                params={
                    "slug": f"eq.{normalized_slug}",
                    "select": "*",
                    "limit": "1",
                },


# grava-3106.7  PATCH /api/courts/{id}/settings — auto-approve toggle
# ---------------------------------------------------------------------------

@method_decorator(csrf_exempt, name="dispatch")
class CourtSettingsView(View):
    """
    PATCH /api/courts/{court_id}/settings

    Allows the court owner to update court settings.
    Currently supports toggling the auto-approve flag for single bookings.

    Request body:
      {"auto_approve_single": true | false}

    Response 200:
      {"court_id": "<uuid>", "auto_approve_single": true | false}

    grava-3106.7 / BCORE-026 / OWNER-44
    """

    def patch(self, request, court_id):
        # --- Auth + owner role check ---
        user, err = _require_owner(request)
        if err is not None:
            return err

        # --- Parse body ---
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        # --- Validate auto_approve_single ---
        if "auto_approve_single" not in body:
            return JsonResponse(
                {"error": "auto_approve_single is required."}, status=400
            )

        auto_approve = body["auto_approve_single"]
        if not isinstance(auto_approve, bool):
            return JsonResponse(
                {"error": "auto_approve_single must be a boolean."}, status=400
            )

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)
        courts_url = f"{supabase_url}/rest/v1/courts"

        # --- Fetch court (verify existence + ownership) ---
        try:
            court_resp = requests.get(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "id,owner_id", "limit": "1"},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

<<<<<<< HEAD
        if resp.status_code != 200:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        rows = resp.json()

        # grava-3106.6.1 — 404 if slug not found
        if not rows:
            return JsonResponse({"error": "Court not found."}, status=404)

        court = rows[0]

        # grava-3106.6.1 — 404 if status is not approved
        if court.get("status") != "approved":
            return JsonResponse({"error": "Court not found."}, status=404)

        return JsonResponse(_court_to_dict(court), status=200)
=======
        if court_resp.status_code != 200:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        court_rows = court_resp.json()
        if not court_rows:
            return JsonResponse({"error": "Court not found."}, status=404)

        court = court_rows[0]
        if court.get("owner_id") != user.id:
            return JsonResponse(
                {"error": "You do not have permission to modify this court."}, status=403
            )

        # --- Update auto_approve_single in Supabase ---
        try:
            patch_resp = requests.patch(
                courts_url,
                params={"id": f"eq.{court_id}", "select": "id,auto_approve_single"},
                json={"auto_approve_single": auto_approve},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Court service unavailable."}, status=503)

        if patch_resp.status_code != 200:
            return JsonResponse({"error": "Failed to update court settings."}, status=503)

        rows = patch_resp.json()
        if not rows:
            return JsonResponse({"error": "Court not found."}, status=404)

        updated = rows[0]
        return JsonResponse(
            {
                "court_id": updated.get("id"),
                "auto_approve_single": updated.get("auto_approve_single", False),
            },
            status=200,
        )
>>>>>>> grava/grava-3106.7

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)
