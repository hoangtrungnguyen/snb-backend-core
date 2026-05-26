# Courts API

Base path: `/api/courts/`

Public `GET` endpoints require no authentication. Write operations (`POST`, `PATCH`, `DELETE`) require a Bearer token with `role = owner`.

---

## GET /api/courts/

List courts with optional filters. Results are paginated and ordered by `created_at DESC`.

**Auth:** None required

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| owner_id | string | no | Filter by owner UUID |
| status | string | no | Filter by status (e.g. `pending`, `approved`, `suspended`) |
| sport_type | string | no | Filter by a sport type contained in `sport_types` array |
| page | int | no | Page number (default: 1) |
| page_size | int | no | Items per page (default: 20) |

**Response `200`:**
```json
{
  "results": [ { "id": "...", "name": "...", "slug": "...", "status": "...", ... } ],
  "page": 1,
  "page_size": 20
}
```

**Errors:**
- `503` — Court service unavailable

---

## POST /api/courts/

Create a new court. Address is geocoded via Google Maps API to populate `lat`/`lng` and canonical address. A URL slug is auto-generated from the court name. The court starts with `status = pending`.

**Auth:** Bearer token required (owner role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| name | string | yes | Court name |
| sport_types | array of string | no | List of sport types |
| capacity | int | no | Max player capacity |
| price_per_hour | float | no | Hourly rate |
| operating_hours | object | no | `{mon: {open: "HH:MM", close: "HH:MM"}, ...}` |
| address | string | no | Physical address (geocoded) |
| amenities | array of string | no | Available amenities |
| description | string | no | Free-text description |
| photos | array of string | no | Photo URLs |

**Response `201`:** Full court object

**Errors:**
- `400` — Missing `name`, invalid `operating_hours` format, or invalid JSON
- `401` — Missing or invalid token
- `403` — Role is not `owner`
- `503` — Court service unavailable

---

## GET /api/courts/nearby

Return approved courts sorted by distance from the caller's location. Courts without geocoded coordinates are excluded. Returns `[]` if no courts are within the radius.

**Auth:** None required

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| lat | float | yes | Caller latitude |
| lng | float | yes | Caller longitude |
| radius_km | int | no | Search radius: `1`, `3`, or `5` (default: `5`) |
| sport | string | no | Filter by sport type |
| price_min | float | no | Minimum `price_per_hour` |
| price_max | float | no | Maximum `price_per_hour` |
| time_of_day | string | no | `morning` (06–12), `afternoon` (12–17), `evening` (17–21), or `night` (21–06) |

**Response `200`:** JSON array of court objects (each includes `distance_km` and `has_open_slots_today`)
```json
[
  {
    "id": "...",
    "name": "...",
    "distance_km": 1.234,
    "has_open_slots_today": true,
    ...
  }
]
```

**Errors:**
- `400` — Missing `lat`/`lng`, invalid numeric values, invalid `radius_km` or `time_of_day`
- `503` — Court service unavailable

---

## GET /api/courts/by-slug/{slug}

Resolve a court slug to its full detail. Case-insensitive. Returns `404` if the court does not exist or its status is not `approved`.

**Auth:** None required

**Response `200`:** Full court object

**Errors:**
- `404` — Slug not found or court not approved
- `503` — Court service unavailable

---

## GET /api/courts/{court_id}/

Return full detail for a single court.

**Auth:** None required

**Response `200`:** Full court object

**Errors:**
- `404` — Court not found
- `503` — Court service unavailable

---

## PATCH /api/courts/{court_id}/

Partial update of a court. Only the authenticated owner of the court may update it. When `address` is updated, geocoding is re-run; if it fails, `lat`/`lng` are cleared and a `207` is returned with a geocoding warning.

**Auth:** Bearer token required (owner role, must own this court)

**Request body (all fields optional):**
| Field | Type | Description |
|-------|------|-------------|
| name | string | New court name |
| sport_types | array of string | Sport types |
| capacity | int | Max capacity |
| price_per_hour | float | Hourly rate |
| operating_hours | object | Operating hours schema |
| address | string | Physical address |
| amenities | array of string | Amenities list |
| description | string | Description |
| photos | array of string | Photo URLs |

**Response `200`:** Updated court object

**Response `207` (geocoding failed):** Updated court object with `warnings` array

**Errors:**
- `400` — Invalid `operating_hours`, no updatable fields provided, or invalid JSON
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Court not found
- `503` — Court service unavailable

---

## DELETE /api/courts/{court_id}/

Soft-delete a court by setting `status = suspended`. Fails if there are active (`pending` or `confirmed`) bookings.

**Auth:** Bearer token required (owner role, must own this court)

**Response `200`:** Updated court object (with `status = "suspended"`)

**Errors:**
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Court not found
- `409` — Court has active bookings and cannot be deleted
- `503` — Court service unavailable

---

## PATCH /api/courts/{court_id}/settings

Toggle the auto-approve setting for single bookings on this court.

**Auth:** Bearer token required (owner role, must own this court)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| auto_approve_single | boolean | yes | `true` to auto-confirm single bookings, `false` for manual approval |

**Response `200`:**
```json
{
  "court_id": "...",
  "auto_approve_single": true
}
```

**Errors:**
- `400` — Missing or non-boolean `auto_approve_single`, or invalid JSON
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Court not found
- `503` — Court service unavailable

---

## GET /api/courts/{court_id}/slots

Return all slots for a court within a date range. Ordered by `start_at ASC`.

**Auth:** None required

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| from | string | yes | Start date `YYYY-MM-DD` (inclusive) |
| to | string | yes | End date `YYYY-MM-DD` (inclusive boundary) |

**Response `200`:**
```json
{
  "results": [
    {
      "id": "...",
      "court_id": "...",
      "start_at": "...",
      "end_at": "...",
      "status": "open",
      "is_owner_slot": false,
      "access_policy": null,
      "max_players": null,
      "blocked_reason": null,
      "booking_id": null,
      "notes": null
    }
  ]
}
```

**Errors:**
- `400` — Missing or invalid `from`/`to` date format
- `404` — Court not found
- `503` — Court or slot service unavailable

---

## POST /api/courts/slots

Create a single slot for a court. The owner must own the court. Slot times must fall within the court's `operating_hours`. Overlapping slots for the same court return `409`.

**Auth:** Bearer token required (owner role, must own the court)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| court_id | string | yes | Court UUID |
| start_at | string | yes | ISO 8601 datetime (UTC) |
| end_at | string | yes | ISO 8601 datetime (must be after `start_at`) |
| status | string | no | `open` (default), `booked`, `blocked`, or `maintenance` |
| is_owner_slot | boolean | no | `true` forces `status = blocked`, bypassing payment (default: `false`) |

**Response `201`:** Slot object

**Errors:**
- `400` — Missing required fields, invalid datetimes, or time outside operating hours
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Court not found
- `409` — Overlapping slot already exists
- `503` — Court or slot service unavailable

---

## PATCH /api/courts/slots/{slot_id}/block

Block a slot. Sets `status = blocked` and optionally stores a `blocked_reason`. Fails if the slot is currently `booked`.

**Auth:** Bearer token required (owner role, must own the slot's court)

**Request body (optional):**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| blocked_reason | string | no | Human-readable reason |

**Response `200`:** Updated slot object

**Errors:**
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Slot not found
- `409` — Slot has an active booking
- `503` — Service unavailable

---

## PATCH /api/courts/slots/{slot_id}/unblock

Unblock a slot. Sets `status = open` and clears `blocked_reason`.

**Auth:** Bearer token required (owner role, must own the slot's court)

**Request body:** None

**Response `200`:** Updated slot object

**Errors:**
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Slot not found
- `503` — Service unavailable

---

## POST /api/courts/{court_id}/recurrence

Generate recurring open slots on a weekly schedule for a court. Date range must not exceed 90 days. Occurrences that overlap existing slots or fall outside `operating_hours` are silently skipped (counted in `skipped`).

**Auth:** Bearer token required (owner role, must own the court)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| days_of_week | array of string | yes | Day keys: `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` |
| start_time | string | yes | Slot start time `HH:MM` (UTC) |
| end_time | string | yes | Slot end time `HH:MM` (must be after `start_time`) |
| from_date | string | yes | First day of recurrence `YYYY-MM-DD` |
| until_date | string | yes | Last day inclusive `YYYY-MM-DD` (max 90 days from `from_date`) |

**Response `200`:**
```json
{
  "created": 8,
  "skipped": 2,
  "slots": [ ... ]
}
```

**Errors:**
- `400` — Missing/invalid fields, invalid day keys, `end_time` before `start_time`, date range exceeds 90 days
- `401` — Missing or invalid token
- `403` — Not the court owner
- `404` — Court not found
- `503` — Court or slot service unavailable
