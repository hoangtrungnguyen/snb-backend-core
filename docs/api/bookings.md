# Bookings API

Base path: `/api/bookings`

All endpoints require a valid Bearer token. Access rules differ per endpoint (player vs. owner).

---

## POST /api/bookings

Atomically create a single-time booking for an open slot. If `courts.auto_approve_single` is `true` and this is not part of a series, the booking is immediately `confirmed`; otherwise it is `pending` and requires owner approval.

**Auth:** Bearer token required (any authenticated role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| slot_id | string | yes | UUID of an open slot |
| booking_series_id | string | no | UUID of a booking series (forces `status = pending`) |
| customer_name | string | no | Display name for the booking |
| customer_phone | string | no | Customer phone number |
| notes | string | no | Free-text notes |

**Response `201`:**
```json
{
  "id": "...",
  "slot_id": "...",
  "user_id": "...",
  "court_id": "...",
  "booking_series_id": null,
  "customer_name": "Jane Doe",
  "customer_phone": null,
  "notes": null,
  "status": "pending",
  "price_per_hour": null,
  "duration_minutes": null,
  "total_price": null,
  "is_auto_approved": false,
  "is_walk_in": false,
  "created_at": "...",
  "updated_at": "..."
}
```

**Errors:**
- `400` — Missing `slot_id` or invalid JSON body
- `401` — Missing or invalid token
- `404` — Slot not found
- `409` — Slot is no longer open (`status != open`)
- `503` — Booking or court service unavailable

---

## POST /api/bookings/manual

Owner creates a manual / walk-in booking for an in-person customer. If no slot exists for the given court/time window, one is auto-created. The booking is always `confirmed` and `is_walk_in = true`.

**Auth:** Bearer token required (owner role, must own the court)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| court_id | string | yes | UUID of the court |
| date | string | yes | Booking date `YYYY-MM-DD` |
| start_time | string | yes | Start time `HH:MM` (UTC) |
| end_time | string | yes | End time `HH:MM` (must be after `start_time`) |
| customer_name | string | no | Customer display name |
| customer_phone | string | no | Customer phone in E.164 format (e.g. `+84901234567`) |
| notes | string | no | Free-text notes |
| price_per_hour_override | float | no | Override the court's default hourly rate |

**Response `201`:** Booking object (with `is_walk_in: true`, `status: "confirmed"`, computed `duration_minutes` and `total_price`)

**Errors:**
- `400` — Missing/invalid fields, `end_time` not after `start_time`, invalid phone format, or invalid JSON
- `401` — Missing or invalid token
- `403` — Not an owner, or does not own the court
- `404` — Court not found
- `409` — Time window already has a non-open slot (`"Giờ này đã có slot"`)
- `503` — Service unavailable

---

## GET /api/bookings/price-estimate

Estimate the price for a given court and time window. Duration is rounded to the nearest 30-minute interval.

**Auth:** Bearer token required (any authenticated role)

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| court_id | string | yes | Court UUID |
| start_at | string | yes | ISO 8601 datetime |
| end_at | string | yes | ISO 8601 datetime (must be after `start_at`) |
| price_override | float | no | Override hourly rate for the estimate |

**Response `200`:**
```json
{
  "duration_minutes": 90,
  "base_price": 150000.0,
  "override_price": null,
  "total": 150000.0
}
```

`base_price` is `null` if the court has no `price_per_hour` set. `override_price` is `null` if `price_override` was not provided. `total` equals `override_price` if provided, otherwise `base_price`.

**Errors:**
- `400` — Missing params, invalid datetimes, `end_at` before `start_at`, or negative `price_override`
- `401` — Missing or invalid token
- `404` — Court not found
- `503` — Court service unavailable

---

## GET /api/bookings/list

Return a paginated list of bookings visible to the authenticated user. Players see only their own bookings; owners see all bookings for their courts.

**Auth:** Bearer token required (player or owner role)

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| court_id | string | no | Filter by court UUID |
| status | string | no | Filter by status: `pending`, `confirmed`, `cancelled`, or `completed` |
| from_date | string | no | Include bookings created on or after `YYYY-MM-DD` |
| to_date | string | no | Include bookings created on or before `YYYY-MM-DD` |
| page | int | no | Page number (default: 1) |
| page_size | int | no | Items per page (default: 20, max: 100) |

**Response `200`:**
```json
{
  "results": [ { ... } ],
  "page": 1,
  "page_size": 20
}
```

**Errors:**
- `401` — Missing or invalid token
- `503` — Booking or court service unavailable

---

## GET /api/bookings/{booking_id}

Return a single booking by ID. Players may only access their own bookings; owners may access any booking for a court they own.

**Auth:** Bearer token required (player or owner role)

**Response `200`:** Booking object

**Errors:**
- `401` — Missing or invalid token
- `403` — Not the booking player and not the court owner
- `404` — Booking not found
- `503` — Service unavailable

---

## PATCH /api/bookings/{booking_id}/status

Transition a booking's status. Restores the linked slot to `open` on cancellation.

**Auth:** Bearer token required (player or owner role)

Allowed transitions:

| From | To | Allowed actors |
|------|----|----------------|
| `pending` | `confirmed` | Court owner only |
| `pending` | `cancelled` | Court owner OR booking player |
| `confirmed` | `cancelled` | Court owner OR booking player |
| `confirmed` | `completed` | Court owner only |

Notifications are sent to the booking player on `confirmed`, `cancelled`, and `completed` transitions.

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | yes | Target status: `confirmed`, `cancelled`, or `completed` |

**Response `200`:** Updated booking object

**Errors:**
- `400` — Missing or invalid `status`, or invalid JSON
- `401` — Missing or invalid token
- `403` — Not authorised for this transition
- `404` — Booking or court not found
- `409` — Transition not allowed from the current status
- `503` — Service unavailable
