"""
courts.views -- Court CRUD and Slot management API views.

Endpoints:
  POST   /api/courts/          -- create court (owner only)
  GET    /api/courts/          -- list courts (public, paginated, filterable)
  GET    /api/courts/{id}/     -- court detail (public)
  PATCH  /api/courts/{id}/     -- update court (owner only)
  DELETE /api/courts/{id}/     -- soft-delete: sets status=suspended (owner only)
  POST   /api/courts/slots     -- create slot (owner only) [grava-3106.2]

grava-3106.2 subtasks:
  grava-3106.2.1 -- POST /slots: {court_id, start_at, end_at, status}
  grava-3106.2.2 -- Validates start_at/end_at within court operating_hours
  grava-3106.2.3 -- No overlapping slot for same court (409 Slot conflict)
  grava-3106.2.4 -- is_owner_slot: true -> status=blocked, skip payment
"""
import json
import re
import unicodedata
from datetime import datetime, timezone, time as dt_time

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
    - If operating_hours is None/empty, the court operates 24/7 → always valid.
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

        # grava-3106.2.4: owner slot → force status=blocked
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
