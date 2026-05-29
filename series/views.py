"""
series.views — Booking series endpoints.

Endpoints:
  POST  /api/booking-series/preview        — Preview recurring occurrences without persisting
  POST  /api/booking-series                — Create a booking series
  GET   /api/booking-series/<id>           — Series detail with occurrences (grava-3432.8)
  PATCH /api/booking-series/<id>/status    — Approve or cancel a series (grava-3432.8)

grava-3432.7 / BCORE-036 acceptance criteria:
  grava-3432.7.1 / BCORE-149: POST /booking-series/preview
    body: {court_id, pattern, days_of_week, start_time, end_time, valid_from,
           end_condition: {type: 'after_n'|'until_date', value}}

  grava-3432.7.2 / BCORE-150: Returns generated occurrences without persisting:
    {occurrences: [{date, start_at, end_at, slot_id, conflict_reason}],
     total_sessions, total_hours, total_price, conflict_count}

  grava-3432.7.3 / BCORE-151: Conflict detection — occurrence conflicts if:
    - No matching open slot exists for that window, OR
    - Slot is already booked|blocked
    - Time window is outside courts.operating_hours

  grava-3432.7.4 / BCORE-152: Auto-creates missing open slots within
    courts.operating_hours if no conflict — generated slots stay open until confirmed

  grava-3432.7.5 / BCORE-153: POST /booking-series
    body: {...same pattern fields..., notes, skipped_dates: [DATE,...]}

  grava-3432.7.6 / BCORE-154: Transaction:
    1. Insert booking_series row with status=pending
    2. For each non-skipped occurrence: insert bookings row, lock+update slot to booked
    3. If any slot lock fails, roll back entire series and return 409 SeriesConflictFailure(count)

  grava-3432.7.7 / BCORE-155: Fixed-appointment series always start as pending
    — courts.auto_approve_single does NOT apply

  grava-3432.7.8 / BCORE-156: Owner receives one notification per series:
    "Yêu cầu lịch cố định mới — [player] · [pattern] · [N buổi]"

  grava-3432.7.9 / BCORE-157: Player response includes series_id
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone as dt_tz

import requests
from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from requests import RequestException as _RequestException
from rest_framework.exceptions import AuthenticationFailed

from auth_ext.rest import user_headers


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_DAYS = frozenset(["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
_WEEKDAY_TO_KEY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # Mon=0, Sun=6
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALID_PATTERNS = frozenset(["weekly"])

_MAX_AFTER_N = 52       # max 52 weekly sessions (approx 1 year)
_MAX_RANGE_DAYS = 365   # max 365 days look-ahead for until_date


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rest_base() -> str:
    """Base URL for Supabase REST calls."""
    return getattr(settings, "SUPABASE_URL", "")


def _authenticate_request(request):
    """
    Decode the Bearer JWT from the Authorization header.

    Returns (SupabaseUser, token) on success, None if no token present.
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
    return SupabaseUser(uid=uid, role=role, token=token), token


def _require_authenticated(request):
    """
    Return (user, None) for any authenticated user, or (None, JsonResponse) on failure.
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
    return user, None


def _fetch_one(url: str, params: dict, headers: dict):
    """
    Fetch a single row from Supabase REST.

    Returns:
        dict    -- row found
        None    -- empty result (not found)
        "error" -- network or non-200 response
    """
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
    except _RequestException:
        return "error"
    if resp.status_code != 200:
        return "error"
    rows = resp.json()
    return rows[0] if rows else None


def _fetch_list(url: str, params: dict, headers: dict):
    """
    Fetch a list of rows from Supabase REST.

    Returns:
        list    -- rows (may be empty)
        "error" -- network or non-200 response
    """
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
    except _RequestException:
        return "error"
    if resp.status_code != 200:
        return "error"
    return resp.json()


def _send_notification(
    supabase_url: str,
    user_token: str,
    *,
    user_id: str,
    title: str,
    body: str,
    related_booking_id: str | None = None,
    related_slot_id: str | None = None,
    related_series_id: str | None = None,
) -> None:
    """Fire-and-forget notification create. Silently ignores all errors.

    Runs in RLS mode (caller's JWT) via the ``create_notification`` SECURITY
    DEFINER RPC — required because a series approval/cancellation notifies the
    *player* (a different user from the acting owner), which no
    ``user_id = auth.uid()`` INSERT policy could permit directly.
    """
    try:
        requests.post(
            f"{supabase_url}/rest/v1/rpc/create_notification",
            json={
                "p_user_id": user_id,
                "p_title": title,
                "p_body": body,
                "p_related_booking_id": related_booking_id,
                "p_related_slot_id": related_slot_id,
                "p_related_series_id": related_series_id,
            },
            headers=user_headers(user_token, prefer=None),
            timeout=5,
        )
    except Exception:
        pass  # notifications are best-effort


def _parse_time_str(t: str):
    """Parse 'HH:MM' string into (hour, minute) tuple."""
    h, m = t.split(":")
    return int(h), int(m)


def _is_within_operating_hours(
    operating_hours: dict | None,
    day_key: str,
    start_time_str: str,
    end_time_str: str,
) -> bool:
    """
    Return True if [start_time_str, end_time_str) falls within operating_hours for day_key.
    If operating_hours is None or day_key is not present, assume open (returns True).
    """
    if not operating_hours:
        return True
    day_hours = operating_hours.get(day_key)
    if not day_hours:
        return True  # day not configured -- assume open

    open_h, open_m = _parse_time_str(day_hours["open"])
    close_h, close_m = _parse_time_str(day_hours["close"])
    start_h, start_m = _parse_time_str(start_time_str)
    end_h, end_m = _parse_time_str(end_time_str)

    open_mins = open_h * 60 + open_m
    close_mins = close_h * 60 + close_m
    start_mins = start_h * 60 + start_m
    end_mins = end_h * 60 + end_m

    return start_mins >= open_mins and end_mins <= close_mins


def _generate_occurrences_dates(
    pattern: str,
    days_of_week: list,
    valid_from: date,
    end_condition: dict,
) -> list:
    """
    Generate a list of occurrence dates based on the pattern and end condition.

    Supports:
      pattern = "weekly"
      end_condition = {"type": "after_n", "value": <int>}
                    | {"type": "until_date", "value": "YYYY-MM-DD"}

    Returns sorted list of dates.
    """
    occ_dates = []
    days_set = set(days_of_week)

    if pattern == "weekly":
        if end_condition["type"] == "after_n":
            n = int(end_condition["value"])
            count = 0
            current = valid_from
            max_iterations = n * 7 * 2 + 14  # safety cap
            iterations = 0
            while count < n and iterations < max_iterations:
                day_key = _WEEKDAY_TO_KEY[current.weekday()]
                if day_key in days_set:
                    occ_dates.append(current)
                    count += 1
                current += timedelta(days=1)
                iterations += 1

        elif end_condition["type"] == "until_date":
            until = date.fromisoformat(end_condition["value"])
            current = valid_from
            while current <= until:
                day_key = _WEEKDAY_TO_KEY[current.weekday()]
                if day_key in days_set:
                    occ_dates.append(current)
                current += timedelta(days=1)

    return occ_dates


def _parse_and_validate_series_body(body: dict):
    """
    Validate and parse the common series request body fields.

    Returns (parsed_data_dict, None) on success,
            (None, JsonResponse 400) on validation failure.
    """
    # --- court_id ---
    court_id = body.get("court_id")
    if not court_id or not isinstance(court_id, str) or not court_id.strip():
        return None, JsonResponse({"error": "court_id is required."}, status=400)
    court_id = court_id.strip()

    # --- pattern ---
    pattern = body.get("pattern")
    if not pattern or not isinstance(pattern, str):
        return None, JsonResponse({"error": "pattern is required."}, status=400)
    pattern = pattern.strip()
    if pattern not in _VALID_PATTERNS:
        return None, JsonResponse(
            {"error": "pattern must be one of: {}.".format(", ".join(sorted(_VALID_PATTERNS)))},
            status=400,
        )

    # --- days_of_week ---
    days_of_week = body.get("days_of_week")
    if not days_of_week or not isinstance(days_of_week, list):
        return None, JsonResponse(
            {"error": "days_of_week is required and must be a non-empty list."},
            status=400,
        )
    if len(days_of_week) == 0:
        return None, JsonResponse(
            {"error": "days_of_week must contain at least one day."},
            status=400,
        )
    invalid_days = [d for d in days_of_week if d not in _VALID_DAYS]
    if invalid_days:
        return None, JsonResponse(
            {
                "error": "Invalid day(s) in days_of_week: {}. Must be one of: {}.".format(
                    invalid_days, sorted(_VALID_DAYS)
                )
            },
            status=400,
        )

    # --- start_time ---
    start_time = body.get("start_time")
    if not start_time or not isinstance(start_time, str) or not _TIME_RE.match(start_time.strip()):
        return None, JsonResponse({"error": "start_time is required (HH:MM)."}, status=400)
    start_time = start_time.strip()

    # --- end_time ---
    end_time = body.get("end_time")
    if not end_time or not isinstance(end_time, str) or not _TIME_RE.match(end_time.strip()):
        return None, JsonResponse({"error": "end_time is required (HH:MM)."}, status=400)
    end_time = end_time.strip()

    # --- end_time > start_time ---
    start_h, start_m = _parse_time_str(start_time)
    end_h, end_m = _parse_time_str(end_time)
    if (end_h * 60 + end_m) <= (start_h * 60 + start_m):
        return None, JsonResponse({"error": "end_time must be after start_time."}, status=400)

    # --- valid_from ---
    valid_from_str = body.get("valid_from")
    if (
        not valid_from_str
        or not isinstance(valid_from_str, str)
        or not _DATE_RE.match(valid_from_str.strip())
    ):
        return None, JsonResponse({"error": "valid_from is required (YYYY-MM-DD)."}, status=400)
    valid_from_str = valid_from_str.strip()
    try:
        valid_from = date.fromisoformat(valid_from_str)
    except ValueError:
        return None, JsonResponse(
            {"error": "valid_from must be a valid date (YYYY-MM-DD)."}, status=400
        )

    # --- end_condition ---
    end_condition = body.get("end_condition")
    if not end_condition or not isinstance(end_condition, dict):
        return None, JsonResponse({"error": "end_condition is required."}, status=400)

    ec_type = end_condition.get("type")
    if ec_type not in ("after_n", "until_date"):
        return None, JsonResponse(
            {"error": "end_condition.type must be 'after_n' or 'until_date'."},
            status=400,
        )

    ec_value = end_condition.get("value")

    if ec_type == "after_n":
        try:
            n = int(ec_value)
        except (TypeError, ValueError):
            return None, JsonResponse(
                {"error": "end_condition.value must be an integer for after_n."},
                status=400,
            )
        if n <= 0:
            return None, JsonResponse(
                {"error": "end_condition.value must be a positive integer for after_n."},
                status=400,
            )
        if n > _MAX_AFTER_N:
            return None, JsonResponse(
                {"error": "end_condition.value must not exceed {} sessions.".format(_MAX_AFTER_N)},
                status=400,
            )
        end_condition = {"type": "after_n", "value": n}

    elif ec_type == "until_date":
        if (
            not ec_value
            or not isinstance(ec_value, str)
            or not _DATE_RE.match(str(ec_value).strip())
        ):
            return None, JsonResponse(
                {"error": "end_condition.value must be a YYYY-MM-DD date for until_date."},
                status=400,
            )
        try:
            until_date = date.fromisoformat(str(ec_value).strip())
        except ValueError:
            return None, JsonResponse(
                {"error": "end_condition.value must be a valid date for until_date."},
                status=400,
            )

        if (until_date - valid_from).days > _MAX_RANGE_DAYS:
            return None, JsonResponse(
                {"error": "Date range must not exceed {} days.".format(_MAX_RANGE_DAYS)},
                status=400,
            )
        end_condition = {"type": "until_date", "value": until_date.isoformat()}

    return {
        "court_id": court_id,
        "pattern": pattern,
        "days_of_week": days_of_week,
        "start_time": start_time,
        "end_time": end_time,
        "valid_from": valid_from,
        "end_condition": end_condition,
    }, None


def _build_slot_timestamps(occ_date: date, start_time: str, end_time: str):
    """Build ISO 8601 UTC timestamps for a slot from a date + time strings."""
    start_at = datetime(
        occ_date.year, occ_date.month, occ_date.day,
        *_parse_time_str(start_time),
        tzinfo=dt_tz.utc,
    ).isoformat()
    end_at = datetime(
        occ_date.year, occ_date.month, occ_date.day,
        *_parse_time_str(end_time),
        tzinfo=dt_tz.utc,
    ).isoformat()
    return start_at, end_at


def _duration_hours(start_time: str, end_time: str) -> float:
    """Compute duration in hours between two HH:MM strings."""
    sh, sm = _parse_time_str(start_time)
    eh, em = _parse_time_str(end_time)
    return ((eh * 60 + em) - (sh * 60 + sm)) / 60.0


# ---------------------------------------------------------------------------
# Preview View
# ---------------------------------------------------------------------------


@method_decorator(csrf_exempt, name="dispatch")
class BookingSeriesPreviewView(View):
    """
    POST /api/booking-series/preview

    Preview recurring booking occurrences without persisting any data.
    """

    def post(self, request):
        # --- Auth ---
        user, err = _require_authenticated(request)
        if err is not None:
            return err

        # --- Parse body ---
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        # --- Validate common fields ---
        parsed, err = _parse_and_validate_series_body(body)
        if err is not None:
            return err

        court_id = parsed["court_id"]
        pattern = parsed["pattern"]
        days_of_week = parsed["days_of_week"]
        start_time = parsed["start_time"]
        end_time = parsed["end_time"]
        valid_from = parsed["valid_from"]
        end_condition = parsed["end_condition"]

        supabase_url = _rest_base()
        headers = user_headers(user.token)

        # --- Fetch court ---
        court = _fetch_one(
            f"{supabase_url}/rest/v1/courts",
            params={
                "id": f"eq.{court_id}",
                "select": "id,owner_id,name,price_per_hour,operating_hours",
                "limit": "1",
            },
            headers=headers,
        )
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)

        operating_hours = court.get("operating_hours")
        price_per_hour = court.get("price_per_hour")
        hour_duration = _duration_hours(start_time, end_time)

        # --- Generate occurrence dates ---
        occ_dates = _generate_occurrences_dates(pattern, days_of_week, valid_from, end_condition)

        # --- Check each occurrence for conflicts ---
        occurrences = []
        conflict_count = 0

        for occ_date in occ_dates:
            day_key = _WEEKDAY_TO_KEY[occ_date.weekday()]
            start_at, end_at = _build_slot_timestamps(occ_date, start_time, end_time)

            # Check operating hours first
            if not _is_within_operating_hours(operating_hours, day_key, start_time, end_time):
                conflict_count += 1
                occurrences.append({
                    "date": occ_date.isoformat(),
                    "start_at": start_at,
                    "end_at": end_at,
                    "slot_id": None,
                    "conflict_reason": "outside_operating_hours",
                })
                continue

            # Look for an existing slot for this window
            existing_slots = _fetch_list(
                f"{supabase_url}/rest/v1/slots",
                params={
                    "court_id": f"eq.{court_id}",
                    "start_at": f"eq.{start_at}",
                    "end_at": f"eq.{end_at}",
                    "select": "id,status",
                    "limit": "1",
                },
                headers=headers,
            )

            if existing_slots == "error":
                return JsonResponse({"error": "Slot service unavailable."}, status=503)

            if not existing_slots:
                # No slot found -- this is a conflict (no open slot available)
                conflict_count += 1
                occurrences.append({
                    "date": occ_date.isoformat(),
                    "start_at": start_at,
                    "end_at": end_at,
                    "slot_id": None,
                    "conflict_reason": "no_open_slot",
                })
            else:
                slot = existing_slots[0]
                slot_status = slot.get("status", "")
                if slot_status != "open":
                    conflict_count += 1
                    occurrences.append({
                        "date": occ_date.isoformat(),
                        "start_at": start_at,
                        "end_at": end_at,
                        "slot_id": slot["id"],
                        "conflict_reason": "slot_{}".format(slot_status),
                    })
                else:
                    occurrences.append({
                        "date": occ_date.isoformat(),
                        "start_at": start_at,
                        "end_at": end_at,
                        "slot_id": slot["id"],
                        "conflict_reason": None,
                    })

        total_sessions = len(occurrences)
        non_conflict = total_sessions - conflict_count
        total_hours = non_conflict * hour_duration
        total_price = (
            round(total_hours * float(price_per_hour), 2)
            if price_per_hour is not None
            else None
        )

        return JsonResponse(
            {
                "occurrences": occurrences,
                "total_sessions": total_sessions,
                "total_hours": total_hours,
                "total_price": total_price,
                "conflict_count": conflict_count,
            },
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# Create View
# ---------------------------------------------------------------------------


@method_decorator(csrf_exempt, name="dispatch")
class BookingSeriesCreateView(View):
    """
    POST /api/booking-series

    Create a booking series: inserts booking_series row, individual booking rows,
    and marks all slots as booked. If any slot is unavailable, rolls back the entire
    series and returns 409 SeriesConflictFailure.

    grava-3432.7.7: Series always start as 'pending'; auto_approve_single does NOT apply.
    """

    def post(self, request):
        # --- Auth ---
        user, err = _require_authenticated(request)
        if err is not None:
            return err

        # --- Parse body ---
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        # --- Validate common fields ---
        parsed, err = _parse_and_validate_series_body(body)
        if err is not None:
            return err

        court_id = parsed["court_id"]
        pattern = parsed["pattern"]
        days_of_week = parsed["days_of_week"]
        start_time = parsed["start_time"]
        end_time = parsed["end_time"]
        valid_from = parsed["valid_from"]
        end_condition = parsed["end_condition"]

        # --- Optional fields ---
        notes = (body.get("notes") or "").strip()
        skipped_dates_raw = body.get("skipped_dates") or []
        if not isinstance(skipped_dates_raw, list):
            return JsonResponse({"error": "skipped_dates must be a list."}, status=400)
        skipped_dates = set(str(d).strip() for d in skipped_dates_raw)

        supabase_url = _rest_base()
        headers = user_headers(user.token)

        # --- Fetch court ---
        court = _fetch_one(
            f"{supabase_url}/rest/v1/courts",
            params={
                "id": f"eq.{court_id}",
                "select": "id,owner_id,name,price_per_hour,operating_hours",
                "limit": "1",
            },
            headers=headers,
        )
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)

        owner_id = court.get("owner_id", "")
        court_name = court.get("name", "")
        price_per_hour = court.get("price_per_hour")
        operating_hours = court.get("operating_hours")
        hour_duration = _duration_hours(start_time, end_time)

        # --- Generate occurrence dates ---
        occ_dates = _generate_occurrences_dates(pattern, days_of_week, valid_from, end_condition)

        # --- Filter out skipped dates ---
        active_dates = [d for d in occ_dates if d.isoformat() not in skipped_dates]

        # --- Resolve / auto-create slots for each active occurrence ---
        slots_to_book = []  # list of (occ_date, slot_id, start_at, end_at)
        conflict_count = 0

        for occ_date in active_dates:
            day_key = _WEEKDAY_TO_KEY[occ_date.weekday()]
            start_at, end_at = _build_slot_timestamps(occ_date, start_time, end_time)

            # Check operating hours
            if not _is_within_operating_hours(operating_hours, day_key, start_time, end_time):
                conflict_count += 1
                continue

            # Look for existing slot
            existing_slots = _fetch_list(
                f"{supabase_url}/rest/v1/slots",
                params={
                    "court_id": f"eq.{court_id}",
                    "start_at": f"eq.{start_at}",
                    "end_at": f"eq.{end_at}",
                    "select": "id,status",
                    "limit": "1",
                },
                headers=headers,
            )

            if existing_slots == "error":
                return JsonResponse({"error": "Slot service unavailable."}, status=503)

            if not existing_slots:
                # grava-3432.7.4: Auto-create missing open slot within operating_hours
                try:
                    create_resp = requests.post(
                        f"{supabase_url}/rest/v1/slots",
                        json={
                            "court_id": court_id,
                            "start_at": start_at,
                            "end_at": end_at,
                            "status": "open",
                        },
                        headers=headers,
                        timeout=10,
                    )
                except _RequestException:
                    return JsonResponse({"error": "Slot service unavailable."}, status=503)

                if create_resp.status_code not in (200, 201):
                    return JsonResponse({"error": "Failed to create slot."}, status=503)

                created_slot_rows = create_resp.json()
                if not created_slot_rows:
                    return JsonResponse({"error": "Failed to create slot."}, status=503)

                slot_id = created_slot_rows[0]["id"]
            else:
                slot = existing_slots[0]
                if slot.get("status") != "open":
                    conflict_count += 1
                    continue
                slot_id = slot["id"]

            slots_to_book.append((occ_date, slot_id, start_at, end_at))

        # --- grava-3432.7.6: Insert booking_series row (status=pending always) ---
        ec_type = end_condition["type"]
        ec_value = end_condition["value"]

        series_insert = {
            "court_id": court_id,
            "user_id": user.id,
            "status": "pending",         # grava-3432.7.7: always pending
            "is_auto_approved": False,   # grava-3432.7.7: auto_approve_single does not apply
            "pattern": pattern,
            "days_of_week": days_of_week,
            "start_time": start_time,
            "end_time": end_time,
            "valid_from": valid_from.isoformat(),
            "end_condition_type": ec_type,
            "end_condition_value": str(ec_value),
            "notes": notes or None,
        }

        try:
            series_resp = requests.post(
                f"{supabase_url}/rest/v1/booking_series",
                json=series_insert,
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Series service unavailable."}, status=503)

        if series_resp.status_code not in (200, 201):
            return JsonResponse({"error": "Failed to create booking series."}, status=503)

        series_rows = series_resp.json()
        if not series_rows:
            return JsonResponse({"error": "Failed to create booking series."}, status=503)

        series = series_rows[0]
        series_id = series.get("id", "")

        # --- grava-3432.7.6: Insert bookings + lock slots, roll back on failure ---
        bookings_created = 0

        def _rollback_series():
            """Delete the series row to roll back the transaction."""
            try:
                requests.delete(
                    f"{supabase_url}/rest/v1/booking_series",
                    params={"id": f"eq.{series_id}"},
                    headers=headers,
                    timeout=10,
                )
            except Exception:
                pass

        for occ_date, slot_id, start_at, end_at in slots_to_book:
            # Verify slot is still open (optimistic lock check)
            current_slot = _fetch_one(
                f"{supabase_url}/rest/v1/slots",
                params={"id": f"eq.{slot_id}", "select": "id,status", "limit": "1"},
                headers=headers,
            )
            if (
                current_slot == "error"
                or current_slot is None
                or current_slot.get("status") != "open"
            ):
                _rollback_series()
                conflict_count += 1
                return JsonResponse(
                    {
                        "error": "SeriesConflictFailure({}): "
                                 "One or more slots became unavailable during booking.".format(
                                     conflict_count
                                 )
                    },
                    status=409,
                )

            # Compute pricing
            duration_minutes = int(hour_duration * 60)
            total_price_booking = (
                round(float(price_per_hour) * hour_duration, 2)
                if price_per_hour is not None
                else None
            )

            booking_insert = {
                "slot_id": slot_id,
                "user_id": user.id,
                "court_id": court_id,
                "booking_series_id": series_id,
                "status": "pending",         # grava-3432.7.7
                "is_auto_approved": False,   # grava-3432.7.7
                "is_walk_in": False,
                "notes": notes or None,
                "price_per_hour": float(price_per_hour) if price_per_hour is not None else None,
                "duration_minutes": duration_minutes,
                "total_price": total_price_booking,
            }

            try:
                booking_resp = requests.post(
                    f"{supabase_url}/rest/v1/bookings",
                    json=booking_insert,
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                _rollback_series()
                return JsonResponse({"error": "Booking service unavailable."}, status=503)

            if booking_resp.status_code not in (200, 201):
                _rollback_series()
                return JsonResponse({"error": "Failed to create booking."}, status=503)

            # Lock slot: update status to "booked" (best-effort)
            try:
                requests.patch(
                    f"{supabase_url}/rest/v1/slots",
                    params={"id": f"eq.{slot_id}", "select": "id"},
                    json={"status": "booked"},
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                pass  # Best-effort

            bookings_created += 1

        # --- grava-3432.7.8: Owner notification (one per series) ---
        n_sessions = bookings_created
        pattern_display = "{} · {}".format(pattern, ", ".join(days_of_week))
        _send_notification(
            supabase_url,
            user.token,
            user_id=owner_id,
            title="Yeu cau lich co dinh moi",
            body=(
                "Yeu cau lich co dinh moi -- "
                "{} . {} . {} buoi".format(user.id, pattern_display, n_sessions)
            ),
            related_series_id=series_id,
        )

        # --- grava-3432.7.9: Response includes series_id ---
        return JsonResponse(
            {
                "series_id": series_id,
                "status": series.get("status", "pending"),
                "court_id": court_id,
                "pattern": pattern,
                "days_of_week": days_of_week,
                "start_time": start_time,
                "end_time": end_time,
                "valid_from": valid_from.isoformat(),
                "end_condition": end_condition,
                "notes": notes or None,
                "bookings_created": bookings_created,
                "created_at": series.get("created_at"),
                "updated_at": series.get("updated_at"),
            },
            status=201,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# Series Detail View (grava-3432.8 / BCORE-037)
# ---------------------------------------------------------------------------

# Booking status → session category mapping
_STATUS_PLAYED = frozenset({"confirmed"})       # confirmed = played (or upcoming)
_STATUS_UPCOMING = frozenset({"pending", "confirmed"})  # upcoming = not yet completed/cancelled
_STATUS_CANCELLED = frozenset({"cancelled"})


def _date_from_iso(ts: str) -> str:
    """Extract 'YYYY-MM-DD' date from an ISO 8601 timestamp string."""
    try:
        return ts[:10]
    except Exception:
        return ""


@method_decorator(csrf_exempt, name="dispatch")
class BookingSeriesDetailView(View):
    """
    GET /api/booking-series/<series_id>

    Returns a series with its occurrence list.  Drives CAPP-055 progress bar.

    Access rules:
      - Series player (user_id = caller): may view their own series.
      - Court owner (court.owner_id = caller): may view any series for their court.

    Response 200:
      {
        "id":                  "<uuid>",
        "court_id":            "<uuid>",
        "court_name":          "<str>",
        "pattern":             "weekly",
        "days_of_week":        ["mon", ...],
        "start_time":          "HH:MM",
        "end_time":            "HH:MM",
        "valid_from":          "YYYY-MM-DD",
        "valid_until":         "YYYY-MM-DD" | null,
        "status":              "pending" | "confirmed" | "cancelled",
        "total_sessions":      <int>,
        "sessions_played":     <int>,   # confirmed bookings
        "sessions_upcoming":   <int>,   # pending + confirmed bookings
        "sessions_cancelled":  <int>,   # cancelled bookings
        "occurrences":         [{"booking_id", "slot_id", "date", "start_at", "end_at", "status"}]
      }

    Error responses:
      401 — missing / invalid token
      403 — not the series player and not the court owner
      404 — series not found
      503 — upstream service unavailable
    """

    def get(self, request, series_id: str):
        user, err = _require_authenticated(request)
        if err is not None:
            return err

        supabase_url = _rest_base()
        headers = user_headers(user.token)

        # --- Fetch series ---
        series = _fetch_one(
            f"{supabase_url}/rest/v1/booking_series",
            params={"id": f"eq.{series_id}", "select": "*", "limit": "1"},
            headers=headers,
        )
        if series == "error":
            return JsonResponse({"error": "Series service unavailable."}, status=503)
        if series is None:
            return JsonResponse({"error": "Booking series not found."}, status=404)

        court_id: str = series.get("court_id", "")
        series_user_id: str = series.get("user_id", "")

        # --- Fetch court (for access control + court name) ---
        court = _fetch_one(
            f"{supabase_url}/rest/v1/courts",
            params={"id": f"eq.{court_id}", "select": "id,owner_id,name", "limit": "1"},
            headers=headers,
        )
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)

        court_owner_id: str = court.get("owner_id", "")
        court_name: str = court.get("name", "")

        # --- Access control ---
        is_series_player = (user.id == series_user_id)
        is_court_owner = (user.id == court_owner_id)
        if not is_series_player and not is_court_owner:
            return JsonResponse(
                {"error": "You do not have access to this booking series."}, status=403
            )

        # --- Fetch bookings for this series ---
        bookings_result = _fetch_list(
            f"{supabase_url}/rest/v1/bookings",
            params={
                "booking_series_id": f"eq.{series_id}",
                "select": "id,slot_id,status",
                "order": "created_at.asc",
            },
            headers=headers,
        )
        if bookings_result == "error":
            return JsonResponse({"error": "Booking service unavailable."}, status=503)

        bookings = bookings_result or []

        # --- Build occurrences list — fetch slot timestamps for each booking ---
        occurrences = []
        sessions_played = 0
        sessions_upcoming = 0
        sessions_cancelled = 0

        for booking in bookings:
            b_status: str = booking.get("status", "")
            b_slot_id: str = booking.get("slot_id", "")

            # Fetch slot to get timestamps
            slot = _fetch_one(
                f"{supabase_url}/rest/v1/slots",
                params={"id": f"eq.{b_slot_id}", "select": "id,start_at,end_at", "limit": "1"},
                headers=headers,
            )
            start_at = ""
            end_at = ""
            if slot and slot != "error":
                start_at = slot.get("start_at", "")
                end_at = slot.get("end_at", "")

            occurrences.append({
                "booking_id": booking.get("id"),
                "slot_id": b_slot_id,
                "date": _date_from_iso(start_at),
                "start_at": start_at,
                "end_at": end_at,
                "status": b_status,
            })

            if b_status in _STATUS_PLAYED:
                sessions_played += 1
            if b_status in _STATUS_UPCOMING:
                sessions_upcoming += 1
            if b_status in _STATUS_CANCELLED:
                sessions_cancelled += 1

        total_sessions = len(bookings)

        # --- Derive valid_until from series metadata ---
        ec_type = series.get("end_condition_type")
        ec_value = series.get("end_condition_value")
        valid_until = None
        if ec_type == "until_date" and ec_value:
            valid_until = str(ec_value)

        return JsonResponse(
            {
                "id": series.get("id"),
                "court_id": court_id,
                "court_name": court_name,
                "pattern": series.get("pattern"),
                "days_of_week": series.get("days_of_week"),
                "start_time": series.get("start_time"),
                "end_time": series.get("end_time"),
                "valid_from": series.get("valid_from"),
                "valid_until": valid_until,
                "status": series.get("status"),
                "total_sessions": total_sessions,
                "sessions_played": sessions_played,
                "sessions_upcoming": sessions_upcoming,
                "sessions_cancelled": sessions_cancelled,
                "occurrences": occurrences,
            },
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# Series Status View (grava-3432.8 / BCORE-037)
# ---------------------------------------------------------------------------

# Valid target statuses for series
_VALID_SERIES_TARGET_STATUSES = frozenset({"confirmed", "cancelled"})

# Allowed transitions: (current_status, target_status) → frozenset of actor roles
# Actor roles: "court_owner" = court owner, "series_player" = the booking's player
_SERIES_TRANSITION_RULES: dict[tuple[str, str], frozenset[str]] = {
    # pending → confirmed: court owner only (OWNER-27)
    ("pending", "confirmed"): frozenset({"court_owner"}),
    # pending → cancelled: court owner OR series player (OWNER-27 / CAPP-056)
    ("pending", "cancelled"): frozenset({"court_owner", "series_player"}),
    # confirmed → cancelled: court owner OR series player
    ("confirmed", "cancelled"): frozenset({"court_owner", "series_player"}),
}


@method_decorator(csrf_exempt, name="dispatch")
class BookingSeriesStatusView(View):
    """
    PATCH /api/booking-series/<series_id>/status

    Transition a booking series status.

    Allowed transitions:
      pending   → confirmed  (court owner only — OWNER-27)
      pending   → cancelled  (court owner OR series player — OWNER-27 / CAPP-056)
      confirmed → cancelled  (court owner OR series player)

    On approve (→ confirmed):
      - All pending bookings in the series → confirmed (slots stay booked).
      - Series row status → confirmed.
      - Player receives notification: "Lịch cố định đã được duyệt"

    On cancel (→ cancelled):
      - All pending/confirmed bookings → cancelled.
      - Each cancelled booking's slot → open (best-effort).
      - Series row status → cancelled.
      - Player receives notification if cancelled by owner.

    Request body: { "status": "confirmed" | "cancelled" }

    Responses:
      200 — updated series summary
      400 — missing/invalid status, or invalid JSON
      401 — no/invalid token
      403 — not authorised for this transition
      404 — series not found
      409 — transition not allowed from current state
      503 — upstream error
    """

    def patch(self, request, series_id: str):
        # --- Auth ---
        user, err = _require_authenticated(request)
        if err is not None:
            return err

        # --- Parse body ---
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

        target_status = body.get("status")
        if not target_status or not isinstance(target_status, str):
            return JsonResponse({"error": "status is required."}, status=400)

        target_status = target_status.strip()
        if target_status not in _VALID_SERIES_TARGET_STATUSES:
            return JsonResponse(
                {
                    "error": (
                        f"Invalid status '{target_status}'. "
                        f"Must be one of: {', '.join(sorted(_VALID_SERIES_TARGET_STATUSES))}."
                    )
                },
                status=400,
            )

        supabase_url = _rest_base()
        headers = user_headers(user.token)

        # --- Fetch series ---
        series = _fetch_one(
            f"{supabase_url}/rest/v1/booking_series",
            params={"id": f"eq.{series_id}", "select": "*", "limit": "1"},
            headers=headers,
        )
        if series == "error":
            return JsonResponse({"error": "Series service unavailable."}, status=503)
        if series is None:
            return JsonResponse({"error": "Booking series not found."}, status=404)

        current_status: str = series.get("status", "")
        series_user_id: str = series.get("user_id", "")
        court_id: str = series.get("court_id", "")

        # --- Fetch court ---
        court = _fetch_one(
            f"{supabase_url}/rest/v1/courts",
            params={"id": f"eq.{court_id}", "select": "id,owner_id,name", "limit": "1"},
            headers=headers,
        )
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)

        court_owner_id: str = court.get("owner_id", "")
        court_name: str = court.get("name", "")

        # --- Determine actor roles ---
        actor_roles: set[str] = set()
        if user.id == court_owner_id:
            actor_roles.add("court_owner")
        if user.id == series_user_id:
            actor_roles.add("series_player")

        # --- Validate transition ---
        transition_key = (current_status, target_status)
        allowed_actors = _SERIES_TRANSITION_RULES.get(transition_key)

        if allowed_actors is None:
            if current_status == target_status:
                return JsonResponse(
                    {"error": f"Series is already in '{current_status}' status."},
                    status=409,
                )
            return JsonResponse(
                {
                    "error": (
                        f"Cannot transition series from '{current_status}' "
                        f"to '{target_status}'."
                    )
                },
                status=409,
            )

        if not (actor_roles & allowed_actors):
            return JsonResponse(
                {"error": "You are not authorised to perform this status transition."},
                status=403,
            )

        # --- Fetch all bookings for this series ---
        bookings_result = _fetch_list(
            f"{supabase_url}/rest/v1/bookings",
            params={
                "booking_series_id": f"eq.{series_id}",
                "select": "id,slot_id,status",
            },
            headers=headers,
        )
        if bookings_result == "error":
            return JsonResponse({"error": "Booking service unavailable."}, status=503)

        bookings = bookings_result or []

        # --- Apply transition ---
        if target_status == "confirmed":
            # Approve: update all pending bookings → confirmed
            for booking in bookings:
                if booking.get("status") != "pending":
                    continue
                booking_id = booking.get("id", "")
                try:
                    requests.patch(
                        f"{supabase_url}/rest/v1/bookings",
                        params={"id": f"eq.{booking_id}", "select": "id"},
                        json={"status": "confirmed"},
                        headers=headers,
                        timeout=10,
                    )
                except _RequestException:
                    pass  # best-effort; individual booking update failure is non-fatal

        elif target_status == "cancelled":
            # Cancel: update all pending/confirmed bookings → cancelled; restore slots → open
            for booking in bookings:
                b_status = booking.get("status", "")
                if b_status not in ("pending", "confirmed"):
                    continue
                booking_id = booking.get("id", "")
                slot_id = booking.get("slot_id", "")

                # Cancel the booking
                try:
                    requests.patch(
                        f"{supabase_url}/rest/v1/bookings",
                        params={"id": f"eq.{booking_id}", "select": "id"},
                        json={"status": "cancelled"},
                        headers=headers,
                        timeout=10,
                    )
                except _RequestException:
                    pass  # best-effort

                # Restore slot → open
                if slot_id:
                    try:
                        requests.patch(
                            f"{supabase_url}/rest/v1/slots",
                            params={"id": f"eq.{slot_id}", "select": "id"},
                            json={"status": "open"},
                            headers=headers,
                            timeout=10,
                        )
                    except _RequestException:
                        pass  # best-effort

        # --- Update series status ---
        try:
            series_patch_resp = requests.patch(
                f"{supabase_url}/rest/v1/booking_series",
                params={"id": f"eq.{series_id}", "select": "*"},
                json={"status": target_status},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Series service unavailable."}, status=503)

        if series_patch_resp.status_code not in (200, 201):
            return JsonResponse({"error": "Failed to update series status."}, status=503)

        updated_rows = series_patch_resp.json()
        if not updated_rows:
            return JsonResponse({"error": "Failed to update series status."}, status=503)

        updated_series = updated_rows[0]

        # --- Notifications ---
        if target_status == "confirmed":
            # Notify player: series approved
            _send_notification(
                supabase_url,
                user.token,
                user_id=series_user_id,
                title="Lịch cố định đã được duyệt",
                body=(
                    f"Lịch cố định của bạn tại {court_name} đã được duyệt."
                ),
                related_series_id=series_id,
            )
        elif target_status == "cancelled":
            # Notify the other party:
            # - If owner cancelled → notify player
            # - If player cancelled → notify owner
            if "court_owner" in actor_roles and "series_player" not in actor_roles:
                # Owner-initiated cancel: notify player
                _send_notification(
                    supabase_url,
                    user.token,
                    user_id=series_user_id,
                    title="Lịch cố định bị huỷ",
                    body=f"Lịch cố định của bạn tại {court_name} đã bị huỷ bởi chủ sân.",
                    related_series_id=series_id,
                )
            elif "series_player" in actor_roles and "court_owner" not in actor_roles:
                # Player-initiated cancel: notify owner
                _send_notification(
                    supabase_url,
                    user.token,
                    user_id=court_owner_id,
                    title="Lịch cố định bị huỷ",
                    body=f"Lịch cố định tại {court_name} đã bị huỷ bởi người chơi.",
                    related_series_id=series_id,
                )
            else:
                # Both roles (owner is also the series player) — notify player
                _send_notification(
                    supabase_url,
                    user.token,
                    user_id=series_user_id,
                    title="Lịch cố định bị huỷ",
                    body=f"Lịch cố định của bạn tại {court_name} đã bị huỷ.",
                    related_series_id=series_id,
                )

        return JsonResponse(
            {
                "id": updated_series.get("id"),
                "court_id": court_id,
                "status": updated_series.get("status"),
            },
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)
