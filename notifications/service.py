"""
notifications.service — In-app notification dispatch (grava-52bc.2).

Public API
----------
dispatch_notification(
    user_id, notif_type, title, body,
    related_booking_id=None, related_series_id=None, deep_link=None
) -> dict

Internal helpers
----------------
_send_fcm_multicast(tokens, title, body, data)  — wraps Firebase Admin SDK
"""
from __future__ import annotations

import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_supabase_keys():
    """Return (supabase_url, service_role_key)."""
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
    service_role_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or anon_key
    return supabase_url, service_role_key


def _supabase_headers(service_role_key: str) -> dict:
    return {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }


def _send_fcm_multicast(tokens: list[str], title: str, body: str, data: dict) -> None:
    """
    Send an FCM multicast push notification to all provided device tokens.

    Uses firebase-admin SDK if available; silently skips if not configured.
    Handles offline/background delivery (grava-52bc.2.3).
    """
    if not tokens:
        return

    try:
        import firebase_admin  # noqa: F401
        from firebase_admin import messaging

        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(content_available=True)
                )
            ),
        )
        response = messaging.send_each_for_multicast(message)
        logger.info(
            "FCM multicast: %d success, %d failure",
            response.success_count,
            response.failure_count,
        )
    except ImportError:
        logger.warning("firebase-admin not installed — FCM dispatch skipped.")
    except Exception as exc:  # noqa: BLE001
        # FCM failure is non-fatal: notification row already inserted.
        logger.error("FCM multicast error: %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def dispatch_notification(
    *,
    user_id: str,
    notif_type: str,
    title: str,
    body: str,
    related_booking_id: Optional[str] = None,
    related_series_id: Optional[str] = None,
    deep_link: Optional[str] = None,
) -> dict:
    """
    Insert a notification row and dispatch FCM push.

    Steps:
    1. POST to /rest/v1/notifications  (grava-52bc.2.1)
       - Fields: user_id, type, title, body, data.deep_link,
                 related_booking_id, related_series_id
       - Supabase Realtime automatically broadcasts INSERT to subscribed
         clients filtered by user_id (grava-52bc.2.2 — no extra code needed).
    2. Fetch recipient fcm_tokens from public.users.
    3. Call _send_fcm_multicast (grava-52bc.2.3) — skipped if no tokens.

    Returns the inserted notification dict.
    Raises RuntimeError on Supabase insert failure.
    """
    supabase_url, service_role_key = _get_supabase_keys()
    notif_endpoint = f"{supabase_url}/rest/v1/notifications"
    users_endpoint = f"{supabase_url}/rest/v1/users"
    headers = _supabase_headers(service_role_key)

    # ------------------------------------------------------------------
    # Step 1 — Insert notification row (grava-52bc.2.1)
    # ------------------------------------------------------------------
    data: dict = {}
    if deep_link:
        data["deep_link"] = deep_link  # grava-52bc.2.4

    payload = {
        "user_id": user_id,
        "type": notif_type,
        "title": title,
        "body": body,
        "data": data,
        "related_booking_id": related_booking_id,
        "related_series_id": related_series_id,
    }

    resp = requests.post(notif_endpoint, json=payload, headers=headers, timeout=10)
    if resp.status_code not in (200, 201):
        raise RuntimeError(
            f"Notification insert failed: HTTP {resp.status_code}"
        )

    rows = resp.json()
    notification = rows[0] if rows else payload

    # ------------------------------------------------------------------
    # Step 2 — Fetch recipient fcm_tokens
    # ------------------------------------------------------------------
    try:
        user_resp = requests.get(
            users_endpoint,
            params={"id": f"eq.{user_id}", "select": "id,fcm_tokens"},
            headers=headers,
            timeout=10,
        )
        user_rows = user_resp.json() if user_resp.status_code == 200 else []
        fcm_tokens = (user_rows[0].get("fcm_tokens") or []) if user_rows else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch fcm_tokens for user %s: %s", user_id, exc)
        fcm_tokens = []

    # ------------------------------------------------------------------
    # Step 3 — FCM multicast (grava-52bc.2.3)
    # ------------------------------------------------------------------
    if fcm_tokens:
        _send_fcm_multicast(
            tokens=fcm_tokens,
            title=title,
            body=body,
            data=data,
        )

    return notification
