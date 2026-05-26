# Players API

Base path: `/api/players/`

All endpoints require a valid Supabase JWT with `role = player` in `app_metadata`, passed as `Authorization: Bearer <token>`.

---

## GET /api/players/me

Return the authenticated player's profile from `public.users`.

**Auth:** Bearer token required (player role)

**Query params:** None

**Response `200`:**
```json
{
  "id": "...",
  "email": "player@example.com",
  "name": "Jane Doe",
  "phone": "+84901234567",
  "role": "player"
}
```

**Errors:**
- `401` — Missing or invalid token
- `403` — Token is valid but role is not `player`
- `404` — User profile not found in `public.users`
- `503` — Upstream service unavailable

---

## PATCH /api/players/me

Update the authenticated player's display name.

**Auth:** Bearer token required (player role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| full_name | string | yes | New display name (must not be blank) |

**Response `200`:**
```json
{
  "id": "...",
  "email": "player@example.com",
  "name": "Jane Doe",
  "phone": "+84901234567",
  "role": "player"
}
```

**Errors:**
- `400` — Missing `full_name`, wrong type, or blank string; invalid JSON body
- `401` — Missing or invalid token
- `403` — Role is not `player`
- `404` — User profile not found
- `503` — Upstream service unavailable

---

## POST /api/players/me/avatar

Upload a JPEG or PNG avatar image (max 2 MB) to Supabase Storage and update `public.users.avatar_url`.

**Auth:** Bearer token required (player role)

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| avatar | file | yes | JPEG or PNG image, max 2 MB |

**Response `200`:**
```json
{
  "avatar_url": "https://..."
}
```

**Errors:**
- `400` — Missing `avatar` field, file exceeds 2 MB, or MIME type is not `image/jpeg` / `image/png`
- `401` — Missing or invalid token
- `403` — Role is not `player`
- `503` — Storage or profile service unavailable

---

## POST /api/players/me/fcm-token

Register a Firebase Cloud Messaging device token for push notifications. Idempotent — registering the same token twice has no effect.

**Auth:** Bearer token required (player role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| token | string | yes | FCM device token (non-empty) |

**Response `200`:**
```json
{}
```

**Errors:**
- `400` — Missing or empty `token`, or invalid JSON body
- `401` — Missing or invalid token
- `403` — Role is not `player`
- `503` — FCM token service unavailable

---

## DELETE /api/players/me/fcm-token

Remove a Firebase Cloud Messaging device token (e.g. on logout).

**Auth:** Bearer token required (player role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| token | string | yes | FCM device token to remove |

**Response `204`:** No content

**Errors:**
- `400` — Missing or empty `token`, or invalid JSON body
- `401` — Missing or invalid token
- `403` — Role is not `player`
- `503` — FCM token service unavailable

---

## PATCH /api/players/me/location

Update the player's current GPS location. Only the most recent location is stored — no history is kept.

**Auth:** Bearer token required (player role)

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| lat | float | yes | Latitude (-90 to 90) |
| lng | float | yes | Longitude (-180 to 180) |

**Response `200`:**
```json
{
  "last_lat": 10.7769,
  "last_lng": 106.7009
}
```

**Errors:**
- `400` — Missing or invalid `lat`/`lng`, or out-of-range values; invalid JSON body
- `401` — Missing or invalid token
- `403` — Role is not `player`
- `404` — User profile not found
- `503` — Upstream service unavailable
