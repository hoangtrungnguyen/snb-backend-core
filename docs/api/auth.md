# Auth API

Base path: `/auth/`

All auth endpoints are public (no token required) unless noted otherwise. The system uses Supabase JWT tokens for authentication throughout the rest of the API.

---

## POST /auth/owner/login

Authenticate as a court owner using email and password.

**Auth:** None required

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | yes | Owner email address |
| password | string | yes | Account password |

**Response `200`:**
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "user": { "id": "...", "email": "...", "email_confirmed_at": "..." }
}
```

**Errors:**
- `400` — Missing `email` or `password`, or invalid JSON body
- `401` — Invalid credentials (wrong email or password)
- `403` — Email not verified, or user does not have `owner` role
- `502` / `503` — Upstream Supabase auth service unavailable

---

## POST /auth/owner/forgot-password

Trigger a password reset email for an owner account.

**Auth:** None required

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | yes | Owner email address |

**Response `200`:**
```json
{
  "message": "If that email exists, a reset link has been sent"
}
```

Always returns `200` regardless of whether the email exists (anti-enumeration).

**Errors:**
- `400` — Missing `email` or invalid JSON body

---

## POST /auth/player/signup

Register a new player account. Sends a confirmation email; the account is inactive until the email is verified.

**Auth:** None required

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | yes | Player email address |
| password | string | yes | Password (min 8 chars, at least 1 letter and 1 digit) |

**Response `201`:**
```json
{
  "message": "Confirmation email sent",
  "user": { "id": "...", "email": "..." }
}
```

**Errors:**
- `400` — Missing fields, invalid JSON, or password fails validation rules
- `409` — Email already registered (`error: "email_already_registered"`) or email belongs to a Google OAuth account (`code: "account_exists_other_provider"`)
- `502` / `503` — Upstream service unavailable

---

## POST /auth/player/login

Authenticate as a player using email and password.

**Auth:** None required

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | yes | Player email address |
| password | string | yes | Account password |

**Response `200`:**
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "user": { "id": "...", "email": "...", "email_confirmed_at": "..." }
}
```

**Errors:**
- `400` — Missing `email` or `password`, or invalid JSON body
- `401` — Invalid credentials (any 4xx from Supabase maps to 401; anti-enumeration)
- `403` — Email not verified
- `503` — Upstream auth service unavailable

---

## POST /auth/player/forgot-password

Trigger a password reset email for a player account.

**Auth:** None required

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | yes | Player email address |

**Response `200`:**
```json
{
  "message": "If that email exists, a reset link has been sent"
}
```

Always returns `200` (anti-enumeration). The reset link redirects to `/auth/callback?type=recovery`.

**Errors:**
- `400` — Missing `email` or invalid JSON body

---

## POST /auth/player/resend-verification

Resend the email verification link to an unverified player. Rate-limited to one request per email per 60 seconds.

**Auth:** None required

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| email | string | yes | Player email address |

**Response `200`:**
```json
{
  "message": "Verification email sent"
}
```

**Errors:**
- `400` — Missing `email` or invalid JSON body
- `429` — Rate limit exceeded; includes `retry_after` (seconds remaining)

---

## GET /auth/player/google

Initiate the Google OAuth flow for player sign-in. Redirects the browser to Supabase's Google OAuth authorization URL.

**Auth:** None required

**Query params:** None

**Response:** `302 Redirect` to Supabase Google OAuth URL

**Errors:**
- `503` — `SUPABASE_URL` not configured

---

## GET /auth/callback

OAuth callback handler. Receives the authorization `code` from Supabase after a Google OAuth flow, exchanges it for tokens, upserts the player's `users` row (with identity merge support for accounts that previously signed up with email/password), then redirects to the frontend with tokens in the URL fragment.

**Auth:** None required

**Query params:**
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| code | string | yes | Authorization code from Supabase OAuth |

**Response:** `302 Redirect` to `{FRONTEND_URL}#access_token=...&refresh_token=...`

**Errors:**
- `400` — Missing `code` or token exchange failed
- `503` — Supabase or user-profile service unavailable

---

## POST /auth/refresh

Exchange a refresh token for a new access/refresh token pair.

**Auth:** None required

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| refresh_token | string | yes | Valid Supabase refresh token |

**Response `200`:**
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "user": { ... }
}
```

**Errors:**
- `400` — Missing `refresh_token` or invalid JSON body
- `401` — Invalid or expired refresh token
- `502` — Upstream service unavailable
