# Slots, Join Requests, and Sports Centers API

This document covers:
- `/api/slots/*` — slot detail, access policy, play-together join flow
- `/api/slot-join-requests/*` — approve/reject join requests
- `/api/sports-centers/*` — sports center schedule

---

## GET /api/slots/open-for-join

Return upcoming open slots near the caller's location sorted by `start_at ASC`. Only slots with `access_policy = open` and `status = open` starting in the future are included.

**Auth:** None required

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| lat | float | yes | Caller latitude |
| lng | float | yes | Caller longitude |
| radius_km | int | no | `1`, `3`, or `5` (default: `5`) |
| sport | string | no | Filter courts by sport type |

**Response `200`:**
```json
{
  "results": [
    {
      "slot_id": "...",
      "court_id": "...",
      "court_name": "Court Alpha",
      "sport": "football",
      "start_at": "2026-06-01T08:00:00+00:00",
      "end_at": "2026-06-01T10:00:00+00:00",
      "max_players": 4,
      "current_players": 2
    }
  ]
}
```

Returns `{"results": []}` when no matching slots are found.

**Errors:**
- `400` — Missing `lat`/`lng`, invalid numeric values, or invalid `radius_km`
- `503` — Court or slot service unavailable

---

## GET /api/slots/{slot_id}

Return full detail for a single slot, including `court_name` and computed `duration_minutes`.

**Auth:** None required

**Response `200`:**
```json
{
  "id": "...",
  "court_id": "...",
  "court_name": "Court Alpha",
  "start_at": "2026-06-01T08:00:00+00:00",
  "end_at": "2026-06-01T10:00:00+00:00",
  "duration_minutes": 120,
  "status": "open",
  "access_policy": null,
  "max_players": null,
  "blocked_reason": null,
  "booking_id": null,
  "notes": null
}
```

**Errors:**
- `404` — Slot not found
- `503` — Slot or court service unavailable

---

## PATCH /api/slots/{slot_id}/access

Set the access policy (`open` or `private`) and optionally `max_players` for a slot. Only the player whose booking is linked to this slot (the "slot booking owner") may call this endpoint.

**Auth:** Bearer token required (any authenticated role; must be the booking owner for this slot)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| access_policy | string | yes | `open` or `private` |
| max_players | int | no | Positive integer; maximum number of join participants |

**Response `200`:**
```json
{
  "id": "...",
  "court_id": "...",
  "access_policy": "open",
  "max_players": 4,
  "status": "booked",
  "start_at": "...",
  "end_at": "..."
}
```

**Errors:**
- `400` — Missing or invalid `access_policy`, non-positive `max_players`, or invalid JSON
- `401` — Missing or invalid token
- `403` — Caller is not the booking owner for this slot
- `404` — Slot not found, or no booking linked to this slot
- `503` — Service unavailable

---

## POST /api/slots/{slot_id}/join

Request to join an open slot. Creates a `slot_join_requests` row with `status = pending`. The slot must have `access_policy = open`.

**Auth:** Bearer token required (any authenticated role)

**Request body:** None

**Response `201`:**
```json
{
  "id": "...",
  "slot_id": "...",
  "user_id": "...",
  "status": "pending",
  "requested_at": "..."
}
```

**Errors:**
- `401` — Missing or invalid token
- `404` — Slot not found
- `409` — Slot is `private`, or caller already has a `pending`/`approved` request for this slot
- `503` — Service unavailable

---

## GET /api/slots/{slot_id}/participants

Return the participant list and pending join requests for a slot. Any authenticated user may view this data.

**Auth:** Bearer token required (any authenticated role)

**Response `200`:**
```json
{
  "slot_id": "...",
  "participants": [
    {
      "id": "...",
      "slot_id": "...",
      "user_id": "...",
      "joined_at": "...",
      "payment_status": "unpaid",
      "payment_method": null
    }
  ],
  "join_requests": [
    {
      "id": "...",
      "slot_id": "...",
      "user_id": "...",
      "status": "pending",
      "requested_at": "..."
    }
  ]
}
```

**Errors:**
- `401` — Missing or invalid token
- `404` — Slot not found
- `503` — Service unavailable

---

## GET /api/slots/{slot_id}/join-status

Return the caller's current join request status for a slot.

**Auth:** Bearer token required (any authenticated role)

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| user_id | string | no | UUID of the player to check (defaults to the authenticated caller) |

**Response `200`:**
```json
{
  "slot_id": "...",
  "user_id": "...",
  "status": "pending"
}
```

`status` is one of: `pending`, `approved`, `rejected`, or `none` (no request exists).

**Errors:**
- `401` — Missing or invalid token
- `404` — Slot not found
- `503` — Service unavailable

---

## POST /api/slots/{slot_id}/last-minute

Mark a slot as last-minute available and dispatch FCM push notifications to nearby players (within 5 km of the court).

**Auth:** Bearer token required (owner role, must own the slot's court)

**Request body:** None

**Response `200`:** Updated slot object (includes `is_last_minute: true`)

**Errors:**
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Slot or court not found
- `503` — Service unavailable

---

## PATCH /api/slot-join-requests/{join_request_id}/approve

Approve a pending join request. Sets the request `status = approved`, inserts a `slot_participants` row, and sends an approval notification to the requester. Only the booking owner of the linked slot may approve.

**Auth:** Bearer token required (any authenticated role; must be the slot booking owner)

**Request body:** None

**Response `200`:**
```json
{
  "id": "...",
  "slot_id": "...",
  "user_id": "...",
  "status": "approved",
  "requested_at": "..."
}
```

**Errors:**
- `401` — Missing or invalid token
- `403` — Caller is not the slot booking owner
- `404` — Join request, slot, or booking not found
- `409` — Request is not in `pending` status
- `503` — Service unavailable

---

## PATCH /api/slot-join-requests/{join_request_id}/reject

Reject a pending join request. Sets `status = rejected` and sends a rejection notification to the requester. Only the booking owner of the linked slot may reject.

**Auth:** Bearer token required (any authenticated role; must be the slot booking owner)

**Request body:** None

**Response `200`:**
```json
{
  "id": "...",
  "slot_id": "...",
  "user_id": "...",
  "status": "rejected",
  "requested_at": "..."
}
```

**Errors:**
- `401` — Missing or invalid token
- `403` — Caller is not the slot booking owner
- `404` — Join request, slot, or booking not found
- `409` — Request is not in `pending` status
- `503` — Service unavailable

---

## GET /api/sports-centers/{sc_id}/schedule

Return all courts belonging to a sports center along with their slots for a given day.

**Auth:** None required

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| date | string | yes | Target date `YYYY-MM-DD` |

**Response `200`:**
```json
{
  "date": "2026-06-01",
  "courts": [
    {
      "id": "...",
      "name": "Court Alpha",
      "status": "approved",
      "slots": [
        {
          "id": "...",
          "start_at": "...",
          "end_at": "...",
          "status": "open",
          "booking_id": null,
          "blocked_reason": null
        }
      ]
    }
  ]
}
```

**Errors:**
- `400` — Missing or invalid `date` format
- `503` — Court or slot service unavailable
