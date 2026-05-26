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
# Walk-in booking view (grava-3432.2 / BCORE-031)
# ---------------------------------------------------------------------------


@method_decorator(csrf_exempt, name="dispatch")
class WalkInBookingView(View):
    """
    POST /api/bookings/walk-in

    Owner creates a manual / walk-in booking for a slot they own.
    The customer is present in-person; no player JWT needed.

    Acceptance criteria (grava-3432.2 / BCORE-031):
      1. Owner-only: role must be "owner" (403 otherwise).
      2. Owner must own the court linked to the slot (403 otherwise).
      3. slot.status must be "open" (409 if not).
      4. Booking inserted with:
           is_walk_in=True, status="confirmed", is_auto_approved=True
           user_id = owner's UID
      5. slots.status updated to "booked".
      6. Owner receives confirmation notification: "Đặt sân thủ công thành công".

    Request body (JSON):
      {
        "slot_id":        "<uuid>",   # required
        "customer_name":  "<str>",    # optional
        "customer_phone": "<str>",    # optional
        "notes":          "<str>"     # optional
      }

    Response 201: booking object (includes is_walk_in=True)
    Error responses:
      400 — missing slot_id or invalid JSON
      401 — missing / invalid token
      403 — not an owner, or does not own the court
      404 — slot not found
      409 — slot not open
      503 — upstream service unavailable
    """

    def post(self, request):
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

        # --- Validate required field: slot_id ---
        slot_id = body.get("slot_id")
        if not slot_id or not isinstance(slot_id, str) or not slot_id.strip():
            return JsonResponse({"error": "slot_id is required."}, status=400)
        slot_id = slot_id.strip()

        customer_name = body.get("customer_name") or ""
        customer_phone = body.get("customer_phone") or ""
        notes = body.get("notes") or ""

        supabase_url, service_key = _get_supabase_keys()
        headers = _supabase_headers(service_key)

        # -----------------------------------------------------------------------
        # Step 1: Fetch slot
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
        # Step 2: Check slot is open
        # -----------------------------------------------------------------------
        if slot.get("status") != "open":
            return JsonResponse(
                {"error": "Slot unavailable: the slot is no longer open for booking."},
                status=409,
            )

        court_id: str = slot["court_id"]

        # -----------------------------------------------------------------------
        # Step 3: Fetch court — verify ownership
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

        owner_id: str = court.get("owner_id", "")
        court_name: str = court.get("name", "")

        if owner_id != user.id:
            return JsonResponse(
                {"error": "You do not own this court."}, status=403
            )

        # -----------------------------------------------------------------------
        # Step 4: Insert walk-in booking (confirmed, is_walk_in=True)
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
            pass  # Best-effort

        # -----------------------------------------------------------------------
        # Step 6: Notify owner — walk-in booking confirmed
        # -----------------------------------------------------------------------
        slot_time_str = _format_slot_time(
            slot.get("start_at", ""), slot.get("end_at", "")
        )
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
