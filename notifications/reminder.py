"""
notifications.reminder — Push booking reminder dispatch (grava-52bc.3).

Public API
----------
fetch_reminder_candidates() -> list[dict]
    Query Supabase REST API for bookings in the T-60 window.

send_booking_reminder(booking: dict) -> None
    Fetch user fcm_tokens, send FCM push with retry-once, mark reminder_sent=true.

process_booking_reminders() -> None
    Orchestrator: fetch candidates, send reminder for each.

Internal helpers
----------------
_send_fcm_multicast  — imported from notifications.service
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from django.conf import settings

from notifications.service import _send_fcm_multicast

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_supabase_keys():
    """Return (supabase_url, service_role_key)."""
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    service_role_key = settings.SUPABASE_SECRET_KEY
    return supabase_url, service_role_key


def _supabase_headers(service_role_key: str) -> dict:
    return {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }


# ---------------------------------------------------------------------------
# Fetch reminder candidates  (grava-52bc.3.2)
# ---------------------------------------------------------------------------


def fetch_reminder_candidates() -> list[dict]:
    """
    Query Supabase for confirmed bookings with start_at in [now+55min, now+65min]
    that have not yet had reminder_sent=true.

    Uses Supabase REST API with a join via PostgREST embedded resource syntax to
    get slot.start_at and court.name/address in one request.

    Returns list of booking dicts (empty list on any error).
    """
    supabase_url, service_role_key = _get_supabase_keys()
    headers = _supabase_headers(service_role_key)

    now = datetime.now(timezone.utc)
    window_start = (now + timedelta(minutes=55)).isoformat()
    window_end = (now + timedelta(minutes=65)).isoformat()

    # Use Supabase PostgREST: embed slots and courts via foreign key relationships.
    # The select parameter uses the resource embedding syntax.
    try:
        resp = requests.get(
            f"{supabase_url}/rest/v1/bookings",
            params={
                "select": (
                    "id,user_id,court_id,slot_id,booking_series_id,reminder_sent,status,"
                    "slots!inner(start_at),"
                    "courts!inner(name,address)"
                ),
                "status": "eq.confirmed",
                "reminder_sent": "eq.false",
                "slots.start_at": f"gte.{window_start}",
            },
            headers=headers,
            timeout=15,
        )
        if resp.status_code != 200:
            logger.error(
                "fetch_reminder_candidates: Supabase returned HTTP %d",
                resp.status_code,
            )
            return []

        raw_rows = resp.json()

        # Flatten embedded relations into top-level keys
        candidates = []
        for row in raw_rows:
            slot = row.get("slots") or {}
            court = row.get("courts") or {}
            start_at = slot.get("start_at", "")

            # Secondary filter: ensure start_at is within the T-60 window
            # (PostgREST may not support the upper bound filter in the same pass)
            if start_at:
                try:
                    start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
                    if not (now + timedelta(minutes=55) <= start_dt <= now + timedelta(minutes=65)):
                        continue
                except ValueError:
                    pass

            candidates.append({
                "id": row.get("id"),
                "user_id": row.get("user_id"),
                "court_id": row.get("court_id"),
                "slot_id": row.get("slot_id"),
                "booking_series_id": row.get("booking_series_id"),
                "reminder_sent": row.get("reminder_sent"),
                "status": row.get("status"),
                "court_name": court.get("name", ""),
                "court_address": court.get("address", ""),
                "start_at": start_at,
            })

        return candidates

    except Exception as exc:  # noqa: BLE001
        logger.error("fetch_reminder_candidates: unexpected error: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Send reminder for a single booking  (grava-52bc.3.3–3.7)
# ---------------------------------------------------------------------------


def send_booking_reminder(booking: dict) -> None:
    """
    Send an FCM push reminder for a single booking and mark reminder_sent=true.

    Steps:
    1. Fetch fcm_tokens for the booking's user_id.
    2. If empty → log and skip (grava-52bc.3.6).
    3. Send FCM via _send_fcm_multicast with retry-once (grava-52bc.3.7).
    4. On success → PATCH bookings.reminder_sent=true (grava-52bc.3.5).
    5. On permanent failure → log error, do NOT update reminder_sent, continue.

    Payload (grava-52bc.3.4):
        title = "Sắp đến giờ chơi"
        body  = "Sân {court_name} lúc {time} — {address}"
        data  = {booking_id, deep_link="/bookings/{id}"}
    """
    supabase_url, service_role_key = _get_supabase_keys()
    headers = _supabase_headers(service_role_key)

    booking_id = booking.get("id")
    user_id = booking.get("user_id")
    court_name = booking.get("court_name") or ""
    court_address = booking.get("court_address") or ""
    start_at = booking.get("start_at") or ""

    # Format time for display
    try:
        start_dt = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        time_str = start_dt.strftime("%H:%M")
    except (ValueError, AttributeError):
        time_str = ""

    # ------------------------------------------------------------------
    # Step 1: Fetch fcm_tokens for user
    # ------------------------------------------------------------------
    fcm_tokens: list[str] = []
    try:
        user_resp = requests.get(
            f"{supabase_url}/rest/v1/customers",
            params={"id": f"eq.{user_id}", "select": "id,fcm_tokens"},
            headers=headers,
            timeout=10,
        )
        user_rows = user_resp.json() if user_resp.status_code == 200 else []
        fcm_tokens = (user_rows[0].get("fcm_tokens") or []) if user_rows else []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "send_booking_reminder: could not fetch fcm_tokens for user %s: %s",
            user_id, exc,
        )

    # ------------------------------------------------------------------
    # Step 2: Skip if no tokens (grava-52bc.3.6)
    # ------------------------------------------------------------------
    if not fcm_tokens:
        logger.info(
            "send_booking_reminder: no fcm_tokens for user %s (booking %s) — skipping",
            user_id, booking_id,
        )
        return

    # ------------------------------------------------------------------
    # Step 3: Build FCM payload (grava-52bc.3.4)
    # ------------------------------------------------------------------
    title = "Sắp đến giờ chơi"
    if time_str and court_address:
        body = f"Sân {court_name} lúc {time_str} — {court_address}"
    elif time_str:
        body = f"Sân {court_name} lúc {time_str}"
    else:
        body = f"Sân {court_name}"

    data = {
        "booking_id": booking_id,
        "deep_link": f"/bookings/{booking_id}",
    }

    # ------------------------------------------------------------------
    # Step 3: Send FCM with retry-once (grava-52bc.3.7)
    # ------------------------------------------------------------------
    sent = False
    for attempt in range(2):
        try:
            _send_fcm_multicast(tokens=fcm_tokens, title=title, body=body, data=data)
            sent = True
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                logger.warning(
                    "send_booking_reminder: FCM attempt %d failed for booking %s: %s — retrying",
                    attempt + 1, booking_id, exc,
                )
            else:
                logger.error(
                    "send_booking_reminder: FCM permanent failure for booking %s: %s — skipping",
                    booking_id, exc,
                )

    if not sent:
        # Permanent failure — do not mark reminder_sent (grava-52bc.3.7)
        return

    # ------------------------------------------------------------------
    # Step 4: Mark reminder_sent=true (grava-52bc.3.5)
    # ------------------------------------------------------------------
    try:
        patch_resp = requests.patch(
            f"{supabase_url}/rest/v1/bookings",
            params={"id": f"eq.{booking_id}"},
            json={"reminder_sent": True},
            headers=headers,
            timeout=10,
        )
        if patch_resp.status_code not in (200, 204):
            logger.error(
                "send_booking_reminder: failed to set reminder_sent for booking %s: HTTP %d",
                booking_id, patch_resp.status_code,
            )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "send_booking_reminder: could not PATCH reminder_sent for booking %s: %s",
            booking_id, exc,
        )


# ---------------------------------------------------------------------------
# Orchestrator  (grava-52bc.3.2 + 3.8)
# ---------------------------------------------------------------------------


def process_booking_reminders() -> None:
    """
    Fetch all reminder candidates and send a push for each.

    Each booking row is treated independently — including series occurrences
    (grava-52bc.3.8). A failure for one booking does not abort the rest.
    """
    candidates = fetch_reminder_candidates()
    logger.info("process_booking_reminders: found %d candidate(s)", len(candidates))

    for booking in candidates:
        booking_id = booking.get("id")
        try:
            send_booking_reminder(booking)
        except Exception as exc:  # noqa: BLE001
            # Defensive catch: individual failure must not block remaining rows
            logger.error(
                "process_booking_reminders: unhandled error for booking %s: %s",
                booking_id, exc,
            )
