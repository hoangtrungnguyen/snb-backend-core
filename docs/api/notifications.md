# Notifications API

Base path: `/api/notifications`

In-app notifications are created automatically by other API actions (booking status changes, join request approvals/rejections, etc.). These endpoints allow users to retrieve and mark notifications as read.

All endpoints require a valid Bearer token. Both `player` and `owner` roles may access their own notifications.

---

## GET /api/notifications

Return a paginated list of notifications for the authenticated user, ordered by `created_at DESC` (newest first).

**Auth:** Bearer token required (player or owner role)

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| page | int | no | Page number (default: 1) |
| limit | int | no | Items per page (default: 20, max: 100) |

**Response `200`:**
```json
{
  "page": 1,
  "limit": 20,
  "results": [
    {
      "id": "...",
      "user_id": "...",
      "type": null,
      "title": "Đặt sân thành công",
      "body": "Đặt sân thành công — Court Alpha · 01/06/2026 · 08:00–10:00",
      "data": {},
      "read": false,
      "related_booking_id": "...",
      "related_series_id": null,
      "created_at": "..."
    }
  ]
}
```

**Errors:**
- `401` — Missing or invalid token
- `503` — Notifications service unavailable

---

## PATCH /api/notifications/{notif_id}/read

Mark a single notification as read. Only updates the notification if its `user_id` matches the authenticated caller (prevents cross-user writes).

**Auth:** Bearer token required (player or owner role)

**Request body:** None

**Response `200`:** Updated notification object
```json
{
  "id": "...",
  "user_id": "...",
  "type": null,
  "title": "...",
  "body": "...",
  "data": {},
  "read": true,
  "related_booking_id": null,
  "related_series_id": null,
  "created_at": "..."
}
```

**Errors:**
- `401` — Missing or invalid token
- `404` — Notification not found (or does not belong to this user)
- `503` — Notifications service unavailable

---

## POST /api/notifications/read-all

Mark all unread notifications as read for the authenticated user.

**Auth:** Bearer token required (player or owner role)

**Request body:** None

**Response `200`:**
```json
{
  "status": "ok"
}
```

**Errors:**
- `401` — Missing or invalid token
- `503` — Notifications service unavailable
