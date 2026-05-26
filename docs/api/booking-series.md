# Booking Series API

Base path: `/api/booking-series`

Booking series represent recurring fixed-schedule bookings (e.g. every Monday at 08:00 for 12 weeks). All endpoints require a valid Bearer token.

Note: Series bookings always start with `status = pending` regardless of `courts.auto_approve_single`. The court owner must manually confirm a series.

---

## POST /api/booking-series/preview

Preview recurring occurrences without persisting any data. Checks each occurrence for conflicts (outside operating hours, no open slot, or slot already booked/blocked).

**Auth:** Bearer token required (any authenticated role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| court_id | string | yes | Court UUID |
| pattern | string | yes | Recurrence pattern — only `"weekly"` is supported |
| days_of_week | array of string | yes | Day keys: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` |
| start_time | string | yes | Slot start time `HH:MM` (UTC) |
| end_time | string | yes | Slot end time `HH:MM` (must be after `start_time`) |
| valid_from | string | yes | First occurrence date `YYYY-MM-DD` |
| end_condition | object | yes | `{"type": "after_n", "value": <int>}` or `{"type": "until_date", "value": "YYYY-MM-DD"}` (max 52 sessions for `after_n`; max 365 days range for `until_date`) |

**Response `200`:**
```json
{
  "occurrences": [
    {
      "date": "2026-06-02",
      "start_at": "2026-06-02T08:00:00+00:00",
      "end_at": "2026-06-02T10:00:00+00:00",
      "slot_id": "...",
      "conflict_reason": null
    },
    {
      "date": "2026-06-09",
      "start_at": "2026-06-09T08:00:00+00:00",
      "end_at": "2026-06-09T10:00:00+00:00",
      "slot_id": null,
      "conflict_reason": "no_open_slot"
    }
  ],
  "total_sessions": 4,
  "total_hours": 8.0,
  "total_price": 400000.0,
  "conflict_count": 1
}
```

`conflict_reason` values: `null` (no conflict), `"outside_operating_hours"`, `"no_open_slot"`, `"slot_booked"`, `"slot_blocked"`.

**Errors:**
- `400` — Missing/invalid fields, invalid day keys, invalid `end_condition`, date range too large, or invalid JSON
- `401` — Missing or invalid token
- `404` — Court not found
- `503` — Court or slot service unavailable

---

## POST /api/booking-series

Create a booking series. Inserts a `booking_series` row (`status = pending`), individual `bookings` rows for each non-skipped occurrence, and marks each slot as `booked`. Missing slots within operating hours are auto-created. If any slot becomes unavailable during the transaction, the entire series is rolled back and `409` is returned.

**Auth:** Bearer token required (any authenticated role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| court_id | string | yes | Court UUID |
| pattern | string | yes | `"weekly"` |
| days_of_week | array of string | yes | Day keys |
| start_time | string | yes | Slot start time `HH:MM` |
| end_time | string | yes | Slot end time `HH:MM` |
| valid_from | string | yes | First occurrence date `YYYY-MM-DD` |
| end_condition | object | yes | Same as preview — `after_n` or `until_date` |
| notes | string | no | Free-text notes |
| skipped_dates | array of string | no | Dates to skip in `YYYY-MM-DD` format |

**Response `201`:**
```json
{
  "series_id": "...",
  "status": "pending",
  "court_id": "...",
  "pattern": "weekly",
  "days_of_week": ["mon", "wed"],
  "start_time": "08:00",
  "end_time": "10:00",
  "valid_from": "2026-06-02",
  "end_condition": { "type": "after_n", "value": 8 },
  "notes": null,
  "bookings_created": 8,
  "created_at": "...",
  "updated_at": "..."
}
```

The court owner receives a notification: `"Yêu cầu lịch cố định mới"`.

**Errors:**
- `400` — Missing/invalid fields or invalid JSON
- `401` — Missing or invalid token
- `404` — Court not found
- `409` — `SeriesConflictFailure(N)` — one or more slots became unavailable during booking
- `503` — Service unavailable

---

## GET /api/booking-series/{series_id}

Return a booking series with its occurrence list and session statistics. The series player or the court owner may access this endpoint.

**Auth:** Bearer token required (series player or court owner)

**Response `200`:**
```json
{
  "id": "...",
  "court_id": "...",
  "court_name": "Court Alpha",
  "pattern": "weekly",
  "days_of_week": ["mon"],
  "start_time": "08:00",
  "end_time": "10:00",
  "valid_from": "2026-06-02",
  "valid_until": "2026-08-25",
  "status": "pending",
  "total_sessions": 12,
  "sessions_played": 0,
  "sessions_upcoming": 12,
  "sessions_cancelled": 0,
  "occurrences": [
    {
      "booking_id": "...",
      "slot_id": "...",
      "date": "2026-06-02",
      "start_at": "...",
      "end_at": "...",
      "status": "pending"
    }
  ]
}
```

**Errors:**
- `401` — Missing or invalid token
- `403` — Not the series player and not the court owner
- `404` — Series or court not found
- `503` — Service unavailable

---

## PATCH /api/booking-series/{series_id}/status

Transition a booking series status. Cascades the status change to all relevant individual bookings and restores slots on cancellation. Notifications are sent to the appropriate party.

**Auth:** Bearer token required (series player or court owner)

Allowed transitions:

| From | To | Allowed actors |
|------|----|----------------|
| `pending` | `confirmed` | Court owner only |
| `pending` | `cancelled` | Court owner OR series player |
| `confirmed` | `cancelled` | Court owner OR series player |

On `confirmed`: all `pending` bookings in the series are set to `confirmed`. The series player receives a notification.

On `cancelled`: all `pending`/`confirmed` bookings are set to `cancelled` and their slots are restored to `open`. The opposing party (owner or player) receives a notification.

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| status | string | yes | `confirmed` or `cancelled` |

**Response `200`:**
```json
{
  "id": "...",
  "court_id": "...",
  "status": "confirmed"
}
```

**Errors:**
- `400` — Missing or invalid `status`, or invalid JSON
- `401` — Missing or invalid token
- `403` — Not authorised for this transition
- `404` — Series or court not found
- `409` — Transition not allowed from the current status
- `503` — Service unavailable
