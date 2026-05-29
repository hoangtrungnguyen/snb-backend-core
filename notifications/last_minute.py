"""
notifications.last_minute — Last-minute slot push notification (grava-52bc.4).

Public API
----------
query_nearby_users(court_lat, court_lng, radius_meters) -> list[dict]
    Query Supabase for users whose last known location is within radius_meters
    of the court. Uses earth_distance Haversine RPC.

dispatch_last_minute_push(slot_id, court_id, court_name, nearby_users) -> None
    For each nearby user not already in slot_push_log, collect FCM tokens,
    send FCM multicast, and insert a slot_push_log row.

Internal helpers
----------------
_send_fcm_multicast  — imported from notifications.service
_check_push_log(slot_id, user_id) -> bool
    Returns True if a push has already been sent for this (slot_id, user_id) pair.
_record_push_log(slot_id, user_id) -> None
    Inserts a row into slot_push_log.
"""
from __future__ import annotations

import logging
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


def _check_push_log(slot_id: str, user_id: str) -> bool:
    """
    Return True if a push has already been sent for (slot_id, user_id).

    Queries slot_push_log for an existing row.
    Returns False on any error (fail-open — allow send).
    """
    supabase_url, service_role_key = _get_supabase_keys()
    headers = _supabase_headers(service_role_key)

    try:
        resp = requests.get(
            f"{supabase_url}/rest/v1/slot_push_log",
            params={
                "slot_id": f"eq.{slot_id}",
                "user_id": f"eq.{user_id}",
                "select": "slot_id,user_id",
                "limit": "1",
            },
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return len(resp.json()) > 0
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_check_push_log: could not query slot_push_log for slot %s user %s: %s",
            slot_id, user_id, exc,
        )
    return False


def _record_push_log(slot_id: str, user_id: str) -> None:
    """
    Insert a row into slot_push_log for (slot_id, user_id).

    Silently logs on failure — push deduplication is best-effort.
    """
    supabase_url, service_role_key = _get_supabase_keys()
    headers = _supabase_headers(service_role_key)

    try:
        resp = requests.post(
            f"{supabase_url}/rest/v1/slot_push_log",
            json={"slot_id": slot_id, "user_id": user_id},
            headers=headers,
            timeout=10,
        )
        if resp.status_code not in (200, 201):
            logger.warning(
                "_record_push_log: insert failed for slot %s user %s: HTTP %d",
                slot_id, user_id, resp.status_code,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_record_push_log: unexpected error for slot %s user %s: %s",
            slot_id, user_id, exc,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def query_nearby_users(
    *,
    court_lat: float,
    court_lng: float,
    radius_meters: int = 5000,
) -> list[dict]:
    """
    Query Supabase for users whose last known location is within radius_meters
    of the court coordinates.

    Uses Supabase RPC that executes:
        SELECT id, fcm_tokens FROM customers
        WHERE earth_distance(
            ll_to_earth(last_lat, last_lng),
            ll_to_earth(court_lat, court_lng)
        ) <= radius_meters
        AND location_updated_at >= now() - interval '24 hours'

    Falls back to REST query filtering by last_lat/last_lng approximation
    if the RPC is not available.

    Returns list of user dicts with id, fcm_tokens.
    Returns [] on any error.

    grava-52bc.4.2, grava-52bc.4.3
    """
    supabase_url, service_role_key = _get_supabase_keys()
    headers = _supabase_headers(service_role_key)

    # -- Primary path: call RPC nearby_users_for_court --
    try:
        resp = requests.post(
            f"{supabase_url}/rest/v1/rpc/nearby_users_for_court",
            json={
                "court_lat": court_lat,
                "court_lng": court_lng,
                "radius_meters": radius_meters,
            },
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            rows = resp.json()
            if isinstance(rows, list):
                logger.info(
                    "query_nearby_users: RPC returned %d user(s) within %dm",
                    len(rows), radius_meters,
                )
                return rows
            # Unexpected shape — fall through to fallback
            logger.warning(
                "query_nearby_users: unexpected RPC response shape: %r", rows
            )
        else:
            logger.warning(
                "query_nearby_users: RPC returned HTTP %d — falling back to REST",
                resp.status_code,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "query_nearby_users: RPC call failed: %s — falling back to REST", exc
        )

    # -- Fallback: REST query with approximate Haversine bounding box --
    # Rough bounding box: 1 degree latitude ≈ 111 km
    # For 5 km: delta_lat ≈ 5000 / 111000
    try:
        delta_lat = radius_meters / 111_000.0
        delta_lng = radius_meters / (111_000.0 * abs(max(0.001, abs(court_lat))) ** 0.5
                                     if False else 111_000.0)
        # Use a simple bounding box approximation for the REST fallback
        lat_min = court_lat - delta_lat
        lat_max = court_lat + delta_lat
        lng_min = court_lng - delta_lng
        lng_max = court_lng + delta_lng

        resp = requests.get(
            f"{supabase_url}/rest/v1/customers",
            params={
                "select": "id,fcm_tokens,last_lat,last_lng",
                "last_lat": f"gte.{lat_min}",
                "last_lng": f"gte.{lng_min}",
            },
            headers=headers,
            timeout=15,
        )
        if resp.status_code == 200:
            rows = resp.json()
            # Apply exact Haversine distance filter in Python
            return _haversine_filter(rows, court_lat, court_lng, radius_meters)
        logger.error(
            "query_nearby_users: REST fallback returned HTTP %d",
            resp.status_code,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.error("query_nearby_users: unexpected error: %s", exc)
        return []


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Compute Haversine distance in meters between two (lat, lng) points.
    """
    import math
    R = 6_371_000  # Earth radius in metres
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _haversine_filter(
    users: list[dict],
    court_lat: float,
    court_lng: float,
    radius_meters: int,
) -> list[dict]:
    """Filter users list to those within radius_meters of (court_lat, court_lng)."""
    result = []
    for user in users:
        lat = user.get("last_lat")
        lng = user.get("last_lng")
        if lat is None or lng is None:
            continue
        try:
            dist = _haversine_distance(float(lat), float(lng), court_lat, court_lng)
            if dist <= radius_meters:
                result.append(user)
        except (TypeError, ValueError):
            continue
    return result


def dispatch_last_minute_push(
    *,
    slot_id: str,
    court_id: str,
    court_name: str,
    nearby_users: list[dict],
) -> None:
    """
    Send FCM push notifications to nearby users for a last-minute slot opening.

    For each nearby user:
    1. Check slot_push_log — skip if already pushed (grava-52bc.4.5).
    2. Collect FCM tokens.
    3. If no tokens → skip (do not log).
    4. Send FCM multicast with deep-link data (grava-52bc.4.4).
    5. Insert slot_push_log row (grava-52bc.4.5).

    FCM data payload:
        screen   = "court_detail"
        court_id = <court_id>
        slot_id  = <slot_id>

    grava-52bc.4.4, grava-52bc.4.5
    """
    # Collect eligible tokens and track which user_ids are covered
    all_tokens: list[str] = []
    eligible_user_ids: list[str] = []

    for user in nearby_users:
        user_id = user.get("id")
        if not user_id:
            continue

        # Rate-limit check (grava-52bc.4.5)
        if _check_push_log(slot_id, user_id):
            logger.info(
                "dispatch_last_minute_push: skipping user %s — already pushed for slot %s",
                user_id, slot_id,
            )
            continue

        tokens = user.get("fcm_tokens") or []
        if not tokens:
            logger.info(
                "dispatch_last_minute_push: user %s has no FCM tokens — skipping",
                user_id,
            )
            continue

        all_tokens.extend(tokens)
        eligible_user_ids.append(user_id)

    if not all_tokens:
        logger.info(
            "dispatch_last_minute_push: no eligible tokens for slot %s — skipping FCM",
            slot_id,
        )
        return

    # FCM multicast (grava-52bc.4.4)
    data = {
        "screen": "court_detail",
        "court_id": court_id,
        "slot_id": slot_id,
    }
    title = "Sân còn chỗ ngay bây giờ!"
    body = f"Sân {court_name} có slot vừa mở — đặt ngay!"

    try:
        _send_fcm_multicast(
            tokens=all_tokens,
            title=title,
            body=body,
            data=data,
        )
        logger.info(
            "dispatch_last_minute_push: FCM sent to %d token(s) for slot %s",
            len(all_tokens), slot_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "dispatch_last_minute_push: FCM dispatch failed for slot %s: %s",
            slot_id, exc,
        )
        return

    # Record push log for each eligible user (grava-52bc.4.5)
    for user_id in eligible_user_ids:
        _record_push_log(slot_id, user_id)
