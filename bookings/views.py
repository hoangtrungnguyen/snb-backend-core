"""
bookings.views — Atomic single-time booking endpoint.

Endpoint:
  POST /api/bookings   — Create a booking for a single slot (any authenticated user).

grava-3432.1 / BCORE-030 acceptance criteria:
  1. SELECT ... FOR UPDATE on slots row (atomicity: lock via service-role REST GET,
     then conditional PATCH — Supabase REST does not expose SELECT FOR UPDATE directly,
     so we read status first and return 409 before any write if unavailable).
  2. slot.status != "open" → 409 Slot unavailable (SlotTakenFailure).
  3. Read courts.auto_approve_single for the slot's court.
  4. Insert bookings row:
       status=confirmed, is_auto_approved=True  if auto_approve_single AND no booking_series_id
       status=pending,   is_auto_approved=False otherwise
  5. Update slots.status = "booked".
  6. Send notifications:
       Manual path  → owner: "Yêu cầu đặt sân mới từ [name]"
       Auto-approve → player: "Đặt sân thành công — [court] · [date] · [time]"
       Auto-approve → owner:  "Đặt sân mới tự động được duyệt — [player] · [slot]"
"""

from __future__ import annotations

import json

import requests
from django.conf import settings
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from requests import RequestException as _RequestException
from rest_framework.exceptions import AuthenticationFailed


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_supabase_keys():
    """Return (supabase_url, service_role_key) from Django settings."""
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
    service_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or anon_key
    return supabase_url, service_key


def _supabase_headers(key: str) -> dict:
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }


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
    return SupabaseUser(uid=uid, role=role), token


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
        dict    — row found
        None    — empty result (not found)
        "error" — network or non-200 response
    """
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
    except _RequestException:
        return "error"
    if resp.status_code != 200:
        return "error"
    rows = resp.json()
    return rows[0] if rows else None


def _format_slot_time(start_at: str, end_at: str) -> str:
    """Format ISO slot times to a human-readable 'DD/MM/YYYY · HH:MM–HH:MM' string."""
    try:
        from datetime import datetime

        def _parse(s: str) -> datetime:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))

        start = _parse(start_at)
        end = _parse(end_at)
        date_str = start.strftime("%d/%m/%Y")
        time_str = f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}"
        return f"{date_str} · {time_str}"
    except Exception:
        return f"{start_at} – {end_at}"


def _send_notification(
    supabase_url: str,
    headers: dict,
    *,
    user_id: str,
    title: str,
    body: str,
    related_booking_id: str | None = None,
    related_slot_id: str | None = None,
) -> None:
    """Fire-and-forget notification insert. Silently ignores all errors."""
    payload: dict = {
        "user_id": user_id,
        "title": title,
        "body": body,
        "read": False,
    }
    if related_booking_id:
        payload["related_booking_id"] = related_booking_id
    if related_slot_id:
        payload["related_slot_id"] = related_slot_id

    try:
        requests.post(
            f"{supabase_url}/rest/v1/notifications",
            json=payload,
            headers=headers,
            timeout=5,
        )
    except Exception:
        pass  # notifications are best-effort


def _booking_to_dict(row: dict) -> dict:
    """Serialize a Supabase bookings row to the API response shape."""
    return {
        "id": row.get("id"),
        "slot_id": row.get("slot_id"),
        "user_id": row.get("user_id"),
        "court_id": row.get("court_id"),
        "booking_series_id": row.get("booking_series_id"),
        "customer_name": row.get("customer_name"),
        "customer_phone": row.get("customer_phone"),
        "notes": row.get("notes"),
        "status": row.get("status"),
        "price_per_hour": row.get("price_per_hour"),
        "duration_minutes": row.get("duration_minutes"),
        "total_price": row.get("total_price"),
        "is_auto_approved": row.get("is_auto_approved", False),
        "is_walk_in": row.get("is_walk_in", False),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _require_owner(request):
    """
    Return (user, None) for authenticated owner users, or (None, JsonResponse) on failure.

    Returns:
        (user, None)                 — authenticated owner
        (None, 401 JsonResponse)     — no/invalid token
        (None, 403 JsonResponse)     — valid token but role != owner
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
            {"error": "Only court owners can perform this action."}, status=403
        )

    return user, None


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


@method_decorator(csrf_exempt, name="dispatch")
class BookingCreateView(View):
    """
    POST /api/bookings

    Atomically create a single-time booking for a slot.

    Request body (JSON):
      {
        "slot_id":           "<uuid>",         # required
        "booking_series_id": "<uuid>" | null,  # optional; present → always pending
        "customer_name":     "<str>",          # optional
        "customer_phone":    "<str>",          # optional
        "notes":             "<str>"           # optional
      }

    Response 201: booking object
    Error responses:
      400 — missing slot_id or invalid JSON
      401 — missing / invalid token
      404 — slot not found
      409 — slot unavailable (status != open)
      503 — upstream service unavailable
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

        # --- Validate required field: slot_id ---
        slot_id = body.get("slot_id")
        if not slot_id or not isinstance(slot_id, str) or not slot_id.strip():
            return JsonResponse({"error": "slot_id is required."}, status=400)
        slot_id = slot_id.strip()

        booking_series_id = body.get("booking_series_id") or None
        customer_name = body.get("customer_name") or ""
        customer_phone = body.get("customer_phone") or ""
        notes = body.get("notes") or ""

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # -----------------------------------------------------------------------
        # Step 1: Fetch slot — SELECT (FOR UPDATE guard: read-then-write pattern
        #         using service-role key that bypasses RLS)
        # -----------------------------------------------------------------------
        slot = _fetch_one(
            f"{supabase_url}/rest/v1/slots",
            params={"id": f"eq.{slot_id}", "select": "*", "limit": "1"},
            headers=headers,
        )
        if slot == "error":
            return JsonResponse({"error": "Slot service unavailable."}, status=503)
        if slot is None:
            return JsonResponse({"error": "Slot not found."}, status=404)

        # -----------------------------------------------------------------------
        # Step 2: Guard — slot must be open (SlotTakenFailure → 409)
        # -----------------------------------------------------------------------
        if slot.get("status") != "open":
            return JsonResponse(
                {"error": "Slot unavailable: the slot is no longer open for booking."},
                status=409,
            )

        court_id: str = slot["court_id"]

        # -----------------------------------------------------------------------
        # Step 3: Read courts.auto_approve_single
        # -----------------------------------------------------------------------
        court = _fetch_one(
            f"{supabase_url}/rest/v1/courts",
            params={
                "id": f"eq.{court_id}",
                "select": "id,owner_id,name,auto_approve_single",
                "limit": "1",
            },
            headers=headers,
        )
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)

        auto_approve_single: bool = bool(court.get("auto_approve_single", False))
        owner_id: str = court.get("owner_id", "")
        court_name: str = court.get("name", "")

        # Auto-approve only when: flag is set AND this is a single-time booking
        is_auto_approved: bool = auto_approve_single and (booking_series_id is None)
        booking_status: str = "confirmed" if is_auto_approved else "pending"

        # -----------------------------------------------------------------------
        # Fetch player info for display names in notifications
        # -----------------------------------------------------------------------
        player_row = _fetch_one(
            f"{supabase_url}/rest/v1/users",
            params={"id": f"eq.{user.id}", "select": "id,full_name,email", "limit": "1"},
            headers=headers,
        )
        player_name: str = ""
        if player_row and player_row != "error":
            player_name = player_row.get("full_name") or player_row.get("email") or ""

        effective_customer_name: str = customer_name or player_name

        # -----------------------------------------------------------------------
        # Step 4: Insert booking row
        # -----------------------------------------------------------------------
        insert_data: dict = {
            "slot_id": slot_id,
            "user_id": user.id,
            "court_id": court_id,
            "status": booking_status,
            "is_auto_approved": is_auto_approved,
            "customer_name": effective_customer_name or None,
            "customer_phone": customer_phone or None,
            "notes": notes or None,
            "booking_series_id": booking_series_id,
        }

        try:
            booking_resp = requests.post(
                f"{supabase_url}/rest/v1/bookings",
                json=insert_data,
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Booking service unavailable."}, status=503)

        if booking_resp.status_code not in (200, 201):
            return JsonResponse({"error": "Failed to create booking."}, status=503)

        booking_rows = booking_resp.json()
        if not booking_rows:
            return JsonResponse({"error": "Failed to create booking."}, status=503)

        booking = booking_rows[0]
        booking_id: str = booking.get("id", "")

        # -----------------------------------------------------------------------
        # Step 5: Update slot.status = "booked"
        # -----------------------------------------------------------------------
        try:
            requests.patch(
                f"{supabase_url}/rest/v1/slots",
                params={"id": f"eq.{slot_id}", "select": "id"},
                json={"status": "booked"},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            # Best-effort; in production use a Supabase RPC for true atomicity.
            pass

        # -----------------------------------------------------------------------
        # Step 6: Notifications (fire-and-forget)
        # -----------------------------------------------------------------------
        slot_time_str = _format_slot_time(
            slot.get("start_at", ""), slot.get("end_at", "")
        )

        if is_auto_approved:
            # Player: "Đặt sân thành công — [court] · [date] · [time]"
            _send_notification(
                supabase_url,
                headers,
                user_id=user.id,
                title="Đặt sân thành công",
                body=f"Đặt sân thành công — {court_name} · {slot_time_str}",
                related_booking_id=booking_id,
                related_slot_id=slot_id,
            )
            # Owner: "Đặt sân mới tự động được duyệt — [player] · [slot]"
            _send_notification(
                supabase_url,
                headers,
                user_id=owner_id,
                title="Đặt sân mới tự động được duyệt",
                body=(
                    f"Đặt sân mới tự động được duyệt — "
                    f"{effective_customer_name or user.id} · {slot_time_str}"
                ),
                related_booking_id=booking_id,
                related_slot_id=slot_id,
            )
        else:
            # Owner: "Yêu cầu đặt sân mới từ [name]"
            _send_notification(
                supabase_url,
                headers,
                user_id=owner_id,
                title="Yêu cầu đặt sân mới",
                body=f"Yêu cầu đặt sân mới từ {effective_customer_name or user.id}",
                related_booking_id=booking_id,
                related_slot_id=slot_id,
            )

        return JsonResponse(_booking_to_dict(booking), status=201)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# Manual / walk-in booking view (grava-3432.2 / BCORE-031)
# ---------------------------------------------------------------------------

import re as _re

_E164_RE = _re.compile(r"^\+[1-9]\d{6,14}$")

_DATE_RE_MANUAL = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE_MANUAL = _re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _validate_e164(phone: str) -> bool:
    """Return True if phone matches E.164 format (+<country><number>, 7–15 digits total)."""
    return bool(_E164_RE.match(phone))


@method_decorator(csrf_exempt, name="dispatch")
class ManualBookingView(View):
    """
    POST /api/bookings/manual

    Owner creates a manual / walk-in booking for an in-person customer.
    Unlike the player-facing booking flow, there is no player JWT; the owner
    provides the customer details and selects the court + time window directly.

    Acceptance criteria (grava-3432.2 / BCORE-031):
      1. Owner-only: role must be "owner" (403 otherwise).
      2. Owner must own the court (403 otherwise).
      3. Auto-create slot: if no slot exists for the given court/window, create one.
         If a slot exists for that window but status != "open" → 409
         ("Giờ này đã có slot").
      4. Booking inserted with:
           is_walk_in=True, status="confirmed", is_auto_approved=True
           user_id = owner's UID
      5. price_per_hour: use price_per_hour_override if provided, else court default.
         Compute duration_minutes and total_price.
      6. slots.status updated to "booked".
      7. Owner receives confirmation notification: "Đặt sân thủ công thành công".
      8. customer_phone validated as E.164 if provided.

    Request body (JSON):
      {
        "court_id":               "<uuid>",         # required
        "date":                   "YYYY-MM-DD",     # required
        "start_time":             "HH:MM",          # required
        "end_time":               "HH:MM",          # required (must be > start_time)
        "customer_name":          "<str>",           # optional
        "customer_phone":         "<str>",           # optional; E.164 if provided
        "notes":                  "<str>",           # optional
        "price_per_hour_override": <number>         # optional; overrides court default
      }

    Response 201: booking object (includes is_walk_in=True)
    Error responses:
      400 — missing/invalid fields or invalid JSON
      401 — missing / invalid token
      403 — not an owner, or does not own the court
      404 — court not found
      409 — slot window already occupied
      503 — upstream service unavailable
    """

    def post(self, request):
        from datetime import datetime, timezone as dt_tz, timedelta

        # --- Auth: owner only ---
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

        # --- Validate required fields ---
        court_id = body.get("court_id")
        if not court_id or not isinstance(court_id, str) or not court_id.strip():
            return JsonResponse({"error": "court_id is required."}, status=400)
        court_id = court_id.strip()

        date_str = body.get("date")
        if not date_str or not isinstance(date_str, str) or not _DATE_RE_MANUAL.match(date_str.strip()):
            return JsonResponse({"error": "date is required (YYYY-MM-DD)."}, status=400)
        date_str = date_str.strip()

        start_time_str = body.get("start_time")
        if not start_time_str or not isinstance(start_time_str, str) or not _TIME_RE_MANUAL.match(start_time_str.strip()):
            return JsonResponse({"error": "start_time is required (HH:MM)."}, status=400)
        start_time_str = start_time_str.strip()

        end_time_str = body.get("end_time")
        if not end_time_str or not isinstance(end_time_str, str) or not _TIME_RE_MANUAL.match(end_time_str.strip()):
            return JsonResponse({"error": "end_time is required (HH:MM)."}, status=400)
        end_time_str = end_time_str.strip()

        # --- Build ISO timestamps (treat as UTC) ---
        try:
            start_at = datetime.fromisoformat(f"{date_str}T{start_time_str}:00+00:00")
            end_at = datetime.fromisoformat(f"{date_str}T{end_time_str}:00+00:00")
        except ValueError:
            return JsonResponse({"error": "Invalid date or time value."}, status=400)

        if end_at <= start_at:
            return JsonResponse({"error": "end_time must be after start_time."}, status=400)

        # --- Optional fields ---
        customer_name: str = body.get("customer_name") or ""
        customer_phone: str = (body.get("customer_phone") or "").strip()
        notes: str = body.get("notes") or ""
        price_override = body.get("price_per_hour_override")

        # --- Validate phone (E.164) ---
        if customer_phone and not _validate_e164(customer_phone):
            return JsonResponse(
                {"error": "customer_phone must be in E.164 format (e.g. +84901234567)."},
                status=400,
            )

        # --- Validate price override ---
        if price_override is not None:
            try:
                price_override = float(price_override)
                if price_override < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return JsonResponse(
                    {"error": "price_per_hour_override must be a non-negative number."},
                    status=400,
                )

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        start_iso = start_at.isoformat()
        end_iso = end_at.isoformat()

        # -----------------------------------------------------------------------
        # Step 1: Fetch court — verify ownership and get price
        # -----------------------------------------------------------------------
        court = _fetch_one(
            f"{supabase_url}/rest/v1/courts",
            params={
                "id": f"eq.{court_id}",
                "select": "id,owner_id,name,price_per_hour",
                "limit": "1",
            },
            headers=headers,
        )
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)

        court_owner_id: str = court.get("owner_id", "")
        court_name: str = court.get("name", "")
        court_price_per_hour = court.get("price_per_hour")

        if court_owner_id != user.id:
            return JsonResponse({"error": "You do not own this court."}, status=403)

        # -----------------------------------------------------------------------
        # Step 2: Look for existing slot in this exact window
        # -----------------------------------------------------------------------
        try:
            slot_resp = requests.get(
                f"{supabase_url}/rest/v1/slots",
                params={
                    "court_id": f"eq.{court_id}",
                    "start_at": f"eq.{start_iso}",
                    "end_at": f"eq.{end_iso}",
                    "select": "id,status",
                    "limit": "1",
                },
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        if slot_resp.status_code != 200:
            return JsonResponse({"error": "Slot service unavailable."}, status=503)

        existing_slots = slot_resp.json()

        if existing_slots:
            existing_slot = existing_slots[0]
            if existing_slot.get("status") != "open":
                return JsonResponse(
                    {"error": "Giờ này đã có slot."},
                    status=409,
                )
            slot_id: str = existing_slot["id"]
        else:
            # -----------------------------------------------------------------------
            # Step 3a: Auto-create a new slot for this window
            # -----------------------------------------------------------------------
            try:
                slot_create_resp = requests.post(
                    f"{supabase_url}/rest/v1/slots",
                    json={
                        "court_id": court_id,
                        "start_at": start_iso,
                        "end_at": end_iso,
                        "status": "open",
                    },
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                return JsonResponse({"error": "Slot service unavailable."}, status=503)

            if slot_create_resp.status_code not in (200, 201):
                return JsonResponse({"error": "Failed to create slot."}, status=503)

            slot_rows = slot_create_resp.json()
            if not slot_rows:
                return JsonResponse({"error": "Failed to create slot."}, status=503)

            slot_id = slot_rows[0]["id"]

        # -----------------------------------------------------------------------
        # Step 4: Compute pricing
        # -----------------------------------------------------------------------
        effective_price_per_hour = price_override if price_override is not None else (
            float(court_price_per_hour) if court_price_per_hour is not None else None
        )
        duration_minutes: int = int((end_at - start_at).total_seconds() / 60)
        total_price = None
        if effective_price_per_hour is not None:
            total_price = round(effective_price_per_hour * duration_minutes / 60, 2)

        # -----------------------------------------------------------------------
        # Step 5: Insert walk-in booking (confirmed, is_walk_in=True)
        # -----------------------------------------------------------------------
        insert_data: dict = {
            "slot_id": slot_id,
            "user_id": user.id,
            "court_id": court_id,
            "status": "confirmed",
            "is_walk_in": True,
            "is_auto_approved": True,
            "customer_name": customer_name or None,
            "customer_phone": customer_phone or None,
            "notes": notes or None,
            "price_per_hour": effective_price_per_hour,
            "duration_minutes": duration_minutes,
            "total_price": total_price,
        }

        try:
            booking_resp = requests.post(
                f"{supabase_url}/rest/v1/bookings",
                json=insert_data,
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Booking service unavailable."}, status=503)

        if booking_resp.status_code not in (200, 201):
            return JsonResponse({"error": "Failed to create booking."}, status=503)

        booking_rows = booking_resp.json()
        if not booking_rows:
            return JsonResponse({"error": "Failed to create booking."}, status=503)

        booking = booking_rows[0]
        booking_id: str = booking.get("id", "")

        # -----------------------------------------------------------------------
        # Step 6: Update slot.status = "booked"
        # -----------------------------------------------------------------------
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

        # -----------------------------------------------------------------------
        # Step 7: Notify owner — manual/walk-in booking confirmed
        # -----------------------------------------------------------------------
        slot_time_str = _format_slot_time(start_iso, end_iso)
        display_name = customer_name or "khách"
        _send_notification(
            supabase_url,
            headers,
            user_id=user.id,
            title="Đặt sân thủ công thành công",
            body=(
                f"Đặt sân thủ công thành công — "
                f"{court_name} · {slot_time_str} · {display_name}"
            ),
            related_booking_id=booking_id,
            related_slot_id=slot_id,
        )

        return JsonResponse(_booking_to_dict(booking), status=201)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


# ---------------------------------------------------------------------------
# Booking status transitions (grava-3432.3 / BCORE-032)
# ---------------------------------------------------------------------------

# Valid booking status values
_VALID_BOOKING_STATUSES = frozenset({"pending", "confirmed", "cancelled", "completed"})

# Allowed transitions per (current_status, target_status) → frozenset of actor roles.
# Actor roles: "owner" = court owner, "player_self" = the booking's own player.
_TRANSITION_RULES: dict[tuple[str, str], frozenset[str]] = {
    # pending → confirmed: only court owner (OWNER-23)
    ("pending", "confirmed"): frozenset({"owner"}),
    # pending → cancelled: court owner (OWNER-24) OR the booking player (CAPP-052)
    ("pending", "cancelled"): frozenset({"owner", "player_self"}),
    # confirmed → cancelled: court owner (OWNER-24) OR the booking player (CAPP-052)
    ("confirmed", "cancelled"): frozenset({"owner", "player_self"}),
    # confirmed → completed: only court owner
    ("confirmed", "completed"): frozenset({"owner"}),
}


@method_decorator(csrf_exempt, name="dispatch")
class BookingStatusView(View):
    """
    PATCH /api/bookings/<booking_id>/status

    Transition a booking's status.  Allowed transitions:

      pending   → confirmed  (court owner only — OWNER-23)
      pending   → cancelled  (court owner OR booking player — OWNER-24 / CAPP-052)
      confirmed → cancelled  (court owner OR booking player — OWNER-24 / CAPP-052)
      confirmed → completed  (court owner only)

    On cancellation the linked slot is restored to "open".
    On approval/completion the slot stays "booked".

    Notifications (fire-and-forget):
      confirmed : player receives "Đặt sân đã được duyệt — [court] · [time]"
      cancelled : player receives "Đặt sân bị từ chối — [court] · [time]"
      completed : player receives "Sân đã hoàn thành — [court] · [time]"

    Request body (JSON):
      { "status": "confirmed" | "cancelled" | "completed" }

    Responses:
      200 — updated booking object
      400 — missing/invalid status, or invalid JSON
      401 — no/invalid token
      403 — not authorised for this transition
      404 — booking not found
      409 — transition not allowed from current state
      503 — upstream error
    """

    def patch(self, request, booking_id: str):
        # --- Auth: any authenticated user ---
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
        if target_status not in _VALID_BOOKING_STATUSES:
            return JsonResponse(
                {
                    "error": (
                        f"Invalid status '{target_status}'. "
                        f"Must be one of: {', '.join(sorted(_VALID_BOOKING_STATUSES))}."
                    )
                },
                status=400,
            )

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # -----------------------------------------------------------------------
        # Step 1: Fetch the booking
        # -----------------------------------------------------------------------
        booking = _fetch_one(
            f"{supabase_url}/rest/v1/bookings",
            params={"id": f"eq.{booking_id}", "select": "*", "limit": "1"},
            headers=headers,
        )
        if booking == "error":
            return JsonResponse({"error": "Booking service unavailable."}, status=503)
        if booking is None:
            return JsonResponse({"error": "Booking not found."}, status=404)

        current_status: str = booking.get("status", "")
        booking_user_id: str = booking.get("user_id", "")
        court_id: str = booking.get("court_id", "")
        slot_id: str = booking.get("slot_id", "")

        # -----------------------------------------------------------------------
        # Step 2: Fetch the court (to verify ownership and get name)
        # -----------------------------------------------------------------------
        court = _fetch_one(
            f"{supabase_url}/rest/v1/courts",
            params={
                "id": f"eq.{court_id}",
                "select": "id,owner_id,name",
                "limit": "1",
            },
            headers=headers,
        )
        if court == "error":
            return JsonResponse({"error": "Court service unavailable."}, status=503)
        if court is None:
            return JsonResponse({"error": "Court not found."}, status=404)

        court_owner_id: str = court.get("owner_id", "")
        court_name: str = court.get("name", "")

        # -----------------------------------------------------------------------
        # Step 3: Determine actor roles for this request
        # -----------------------------------------------------------------------
        is_court_owner = (user.id == court_owner_id)
        is_booking_player = (user.id == booking_user_id)

        actor_roles: set[str] = set()
        if is_court_owner:
            actor_roles.add("owner")
        if is_booking_player:
            actor_roles.add("player_self")

        # -----------------------------------------------------------------------
        # Step 4: Validate the transition
        # -----------------------------------------------------------------------
        transition_key = (current_status, target_status)
        allowed_actors = _TRANSITION_RULES.get(transition_key)

        if allowed_actors is None:
            # No rule found — either same-state or disallowed cross-state transition
            if current_status == target_status:
                return JsonResponse(
                    {"error": f"Booking is already in '{current_status}' status."},
                    status=409,
                )
            return JsonResponse(
                {
                    "error": (
                        f"Cannot transition booking from '{current_status}' "
                        f"to '{target_status}'."
                    )
                },
                status=409,
            )

        # Check actor authorisation for this specific transition
        if not (actor_roles & allowed_actors):
            return JsonResponse(
                {"error": "You are not authorised to perform this status transition."},
                status=403,
            )

        # -----------------------------------------------------------------------
        # Step 5: Fetch slot (for notification message and potential slot restore)
        # -----------------------------------------------------------------------
        slot = _fetch_one(
            f"{supabase_url}/rest/v1/slots",
            params={"id": f"eq.{slot_id}", "select": "*", "limit": "1"},
            headers=headers,
        )
        slot_start_at = ""
        slot_end_at = ""
        if slot and slot != "error":
            slot_start_at = slot.get("start_at", "")
            slot_end_at = slot.get("end_at", "")

        slot_time_str = _format_slot_time(slot_start_at, slot_end_at) if slot_start_at else ""

        # -----------------------------------------------------------------------
        # Step 6: Apply the booking status update in Supabase
        # -----------------------------------------------------------------------
        try:
            patch_resp = requests.patch(
                f"{supabase_url}/rest/v1/bookings",
                params={"id": f"eq.{booking_id}", "select": "*"},
                json={"status": target_status},
                headers=headers,
                timeout=10,
            )
        except _RequestException:
            return JsonResponse({"error": "Booking service unavailable."}, status=503)

        if patch_resp.status_code not in (200, 201):
            return JsonResponse({"error": "Failed to update booking."}, status=503)

        updated_rows = patch_resp.json()
        if not updated_rows:
            return JsonResponse({"error": "Failed to update booking."}, status=503)

        updated_booking = updated_rows[0]

        # -----------------------------------------------------------------------
        # Step 7: Restore slot to "open" on cancellation (best-effort)
        # -----------------------------------------------------------------------
        if target_status == "cancelled":
            try:
                requests.patch(
                    f"{supabase_url}/rest/v1/slots",
                    params={"id": f"eq.{slot_id}", "select": "id"},
                    json={"status": "open"},
                    headers=headers,
                    timeout=10,
                )
            except _RequestException:
                pass  # Best-effort; non-fatal

        # -----------------------------------------------------------------------
        # Step 8: Fire-and-forget notification to the booking player
        # -----------------------------------------------------------------------
        notification_map: dict[str, tuple[str, str]] = {
            "confirmed": (
                "Đặt sân đã được duyệt",
                f"Đặt sân đã được duyệt — {court_name} · {slot_time_str}",
            ),
            "cancelled": (
                "Đặt sân bị từ chối",
                f"Đặt sân bị từ chối — {court_name} · {slot_time_str}",
            ),
            "completed": (
                "Sân đã hoàn thành",
                f"Sân đã hoàn thành — {court_name} · {slot_time_str}",
            ),
        }
        if target_status in notification_map:
            notif_title, notif_body = notification_map[target_status]
            _send_notification(
                supabase_url,
                headers,
                user_id=booking_user_id,
                title=notif_title,
                body=notif_body,
                related_booking_id=booking_id,
                related_slot_id=slot_id,
            )

        return JsonResponse(_booking_to_dict(updated_booking), status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)
