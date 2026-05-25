"""
auth_ext.views — Authentication views for court owners and players.

POST /auth/owner/login
    Accepts {"email": "...", "password": "..."}
    Proxies to Supabase Auth signInWithPassword (POST /auth/v1/token?grant_type=password)
    Validates that the authenticated user has role='owner' in the users table.
    Returns {"access_token": "...", "refresh_token": "...", "user": {...}}
    Pure JWT flow — no Django session/cookie auth.

POST /auth/owner/forgot-password
    Accepts {"email": "..."}
    Calls Supabase Auth resetPasswordForEmail (POST /auth/v1/recover)
    Always returns HTTP 200 {"message": "If that email exists, a reset link has been sent"}
    Anti-enumeration: response is identical whether email exists or not.
    Uses SUPABASE_ANON_KEY (not service role key).

POST /auth/player/signup
    Accepts {"email": "...", "password": "..."}
    Validates password: min 8 chars, >=1 letter, >=1 digit
    Proxies to Supabase Auth signUp (POST /auth/v1/signup)
    Returns 201 {"message": "Confirmation email sent", "user": {"id": "...", "email": "..."}}
    Returns 409 {"error": "email_already_registered"} if Supabase returns 422
"""
import json
import logging
import math
import time

import requests
from django.core.cache import cache
from django.conf import settings
from django.http import JsonResponse, HttpResponseRedirect
from urllib.parse import urlencode
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator


@method_decorator(csrf_exempt, name="dispatch")
class OwnerLoginView(View):
    """Handle POST /auth/owner/login by delegating to Supabase Auth."""

    def _check_owner_role(self, user_id):
        """
        Query the users table via Supabase REST API using the service-role key.

        Returns None if the user has role='owner' (check passed).
        Returns a 403 JsonResponse if the user is not found or has a non-owner role.
        """
        supabase_url = getattr(settings, "SUPABASE_URL", "")
        service_role_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")

        users_endpoint = f"{supabase_url}/rest/v1/users"

        try:
            resp = requests.get(
                users_endpoint,
                params={"select": "role", "id": f"eq.{user_id}"},
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException:
            # Network failure — we cannot verify the role; signal upstream error.
            return JsonResponse(
                {"error": "service_unavailable", "detail": "Role check failed"},
                status=503,
            )

        try:
            rows = resp.json()
        except ValueError:
            rows = []

        if not isinstance(rows, list) or len(rows) == 0 or rows[0].get("role") != "owner":
            return JsonResponse(
                {"error": "forbidden", "detail": "Owner role required"},
                status=403,
            )

        return None

    def post(self, request):
        # Parse JSON body
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        email = body.get("email")
        password = body.get("password")

        if not email:
            return JsonResponse({"error": "email is required."}, status=400)
        if not password:
            return JsonResponse({"error": "password is required."}, status=400)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

        token_endpoint = f"{supabase_url}/auth/v1/token"

        try:
            supabase_resp = requests.post(
                token_endpoint,
                params={"grant_type": "password"},
                json={"email": email, "password": password},
                headers={
                    "apikey": supabase_anon_key,
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            return JsonResponse(
                {"error": "Authentication service unavailable.", "detail": str(exc)},
                status=502,
            )

        if supabase_resp.status_code == 200:
            data = supabase_resp.json()
            user = data.get("user") or {}

            # Enforce email verification: reject logins where email_confirmed_at
            # is null or absent.  Supabase sets this field to a timestamp string
            # once the user clicks the confirmation link; before that it is None.
            if not user.get("email_confirmed_at"):
                return JsonResponse(
                    {
                        "error": "email_not_verified",
                        "detail": "Please verify your email before logging in.",
                    },
                    status=403,
                )

            # Validate that the authenticated user has role='owner' in the users table.
            # This check must happen BEFORE returning tokens to the caller.
            user_id = user.get("id", "")
            role_check_result = self._check_owner_role(user_id)
            if role_check_result is not None:
                return role_check_result

            return JsonResponse(
                {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "user": user,
                },
                status=200,
            )

        # Supabase returned an error — map any 4xx to a generic 401.
        # We deliberately do NOT expose Supabase's error_description to prevent
        # user-enumeration attacks (wrong email vs wrong password must be indistinguishable).
        if 400 <= supabase_resp.status_code < 500:
            return JsonResponse(
                {"error": "invalid_credentials", "detail": "Invalid credentials"},
                status=401,
            )

        return JsonResponse(
            {"error": "Authentication service error.", "detail": "Upstream authentication service returned an error."},
            status=502,
        )


_RESET_LINK_SENT_MSG = "If that email exists, a reset link has been sent"


@method_decorator(csrf_exempt, name="dispatch")
class OwnerForgotPasswordView(View):
    """Handle POST /auth/owner/forgot-password by delegating to Supabase Auth.

    Always returns HTTP 200 regardless of whether the email exists or whether
    Supabase itself returns an error — prevents user enumeration attacks.
    """

    def post(self, request):
        # Parse JSON body
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        email = body.get("email")
        if not email:
            return JsonResponse({"error": "email is required."}, status=400)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

        recover_endpoint = f"{supabase_url}/auth/v1/recover"

        try:
            requests.post(
                recover_endpoint,
                json={"email": email},
                headers={
                    "apikey": supabase_anon_key,
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException:
            # Intentionally swallow — anti-enumeration requires always-200
            pass

        return JsonResponse({"message": _RESET_LINK_SENT_MSG}, status=200)


logger = logging.getLogger(__name__)

# Allowlist for token_type values returned by Supabase.
# We accept "bearer" and session-level types; reject anything unexpected.
ALLOWED_TOKEN_TYPES = frozenset({"bearer", "email", "signup", "recovery", "invite"})


@method_decorator(csrf_exempt, name="dispatch")
class TokenRefreshView(View):
    """Handle POST /auth/refresh by exchanging a refresh token via Supabase Auth.

    Accepts {"refresh_token": "..."} and calls Supabase
    POST /auth/v1/token?grant_type=refresh_token.

    Returns:
        200 {"access_token": "...", "refresh_token": "...", "user": {...}} on success
        400 on missing/malformed request body
        401 {"error": "invalid_token"} on invalid or expired refresh token
    """

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        refresh_token = body.get("refresh_token")
        if not refresh_token:
            return JsonResponse({"error": "refresh_token is required."}, status=400)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")

        token_endpoint = f"{supabase_url}/auth/v1/token"

        try:
            supabase_resp = requests.post(
                token_endpoint,
                params={"grant_type": "refresh_token"},
                json={"refresh_token": refresh_token},
                headers={
                    "apikey": supabase_anon_key,
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.error("Token refresh network error: %s", exc)
            return JsonResponse({"error": "service_unavailable"}, status=502)

        if supabase_resp.status_code == 200:
            data = supabase_resp.json()
            return JsonResponse(
                {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "user": data.get("user"),
                },
                status=200,
            )

        if supabase_resp.status_code in (400, 401, 422):
            return JsonResponse({"error": "invalid_token"}, status=401)

        return JsonResponse({"error": "Authentication service unavailable."}, status=502)


# ---------------------------------------------------------------------------
# Player auth
# ---------------------------------------------------------------------------

def _validate_password(password: str) -> dict | None:
    """
    Validate password rules:
    - min 8 characters
    - at least 1 letter
    - at least 1 digit

    Returns None if valid, or a dict describing the failures.
    """
    errors = {}
    if len(password) < 8:
        errors["length"] = "Password must be at least 8 characters."
    if not any(c.isalpha() for c in password):
        errors["letter"] = "Password must contain at least one letter."
    if not any(c.isdigit() for c in password):
        errors["digit"] = "Password must contain at least one digit."
    return errors if errors else None


def _check_google_oauth_provider(supabase_url: str, service_role_key: str, email: str) -> bool:
    """
    Query the Supabase Admin API to check if the given email is linked to a
    Google OAuth identity provider.

    Returns True if the user has a 'google' provider identity, False otherwise
    (including on any error — fail-safe: don't disclose more than we must).
    """
    if not service_role_key:
        logger.warning(
            "SUPABASE_SERVICE_ROLE_KEY not set; cannot check identity provider for email=%s",
            email,
        )
        return False

    admin_endpoint = f"{supabase_url}/auth/v1/admin/users"
    try:
        resp = requests.get(
            admin_endpoint,
            params={"email": email},
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.error(
            "Network error querying Supabase admin users API for email=%s: %s",
            email,
            exc,
        )
        return False

    if resp.status_code != 200:
        logger.error(
            "Supabase admin users API returned %s for email=%s",
            resp.status_code,
            email,
        )
        return False

    try:
        data = resp.json()
    except ValueError:
        logger.error("Failed to parse Supabase admin users API response for email=%s", email)
        return False

    users = data.get("users", [])
    for user in users:
        identities = user.get("identities") or []
        for identity in identities:
            if identity.get("provider") == "google":
                return True

    return False


@method_decorator(csrf_exempt, name="dispatch")
class PlayerSignupView(View):
    """Handle POST /auth/player/signup by delegating to Supabase Auth signUp."""

    def post(self, request):
        # Parse JSON body
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        email = body.get("email")
        password = body.get("password")

        if not email:
            return JsonResponse({"error": "email is required."}, status=400)
        if not password:
            return JsonResponse({"error": "password is required."}, status=400)

        # Password validation before calling Supabase
        validation_errors = _validate_password(password)
        if validation_errors:
            return JsonResponse(
                {"error": "validation_error", "detail": validation_errors},
                status=400,
            )

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
        supabase_service_role_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")

        signup_endpoint = f"{supabase_url}/auth/v1/signup"

        try:
            supabase_resp = requests.post(
                signup_endpoint,
                json={"email": email, "password": password},
                headers={
                    "apikey": supabase_anon_key,
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.error("Network error reaching Supabase signup endpoint: %s", exc)
            return JsonResponse(
                {"error": "Authentication service unavailable."},
                status=503,
            )

        if supabase_resp.status_code == 200:
            data = supabase_resp.json()
            return JsonResponse(
                {
                    "message": "Confirmation email sent",
                    "user": {
                        "id": data.get("id"),
                        "email": data.get("email"),
                    },
                },
                status=201,
            )

        # Supabase 422 means the email is already registered.
        # Determine whether the existing account uses Google OAuth or email/password.
        if supabase_resp.status_code == 422:
            has_google = _check_google_oauth_provider(
                supabase_url, supabase_service_role_key, email
            )
            if has_google:
                return JsonResponse(
                    {"code": "account_exists_other_provider"},
                    status=409,
                )
            return JsonResponse(
                {"error": "email_already_registered"},
                status=409,
            )

        # Other Supabase errors — log server-side, return generic message
        try:
            error_data = supabase_resp.json()
        except ValueError:
            error_data = {}

        logger.error(
            "Supabase signup returned unexpected status %s: %s",
            supabase_resp.status_code,
            error_data,
        )
        return JsonResponse(
            {"error": "Signup failed."},
            status=503,
        )


@method_decorator(csrf_exempt, name="dispatch")
class AuthCallbackView(View):
    """Handle GET /auth/callback — OAuth redirect handler.

    Receives the authorization code from Supabase after an OAuth flow (e.g.
    Google sign-in), exchanges it for access+refresh tokens via the Supabase
    PKCE token endpoint, upserts a `users` row in the database (via the
    Supabase Admin REST API) with role='player', then redirects to the
    configured FRONTEND_URL with the tokens in the URL fragment.

    Security guarantees:
    - Tokens are placed in the URL *fragment* (#), never in query params (?).
      Fragment values are not sent to the server and do not appear in Referer
      headers or server logs.
    - token_type returned by Supabase is validated against an allowlist.
    - No internal error details are returned in response bodies.
    - Network errors map to 503; Supabase auth errors map to 400.
    """

    def get(self, request):
        code = request.GET.get("code", "").strip()
        if not code:
            return JsonResponse({"error": "Missing required parameter: code."}, status=400)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
        supabase_service_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "")
        frontend_url = getattr(settings, "FRONTEND_URL", "/")

        # ------------------------------------------------------------------
        # Step 1: Exchange authorization code for tokens (PKCE flow)
        # ------------------------------------------------------------------
        token_endpoint = f"{supabase_url}/auth/v1/token"
        try:
            token_resp = requests.post(
                token_endpoint,
                params={"grant_type": "pkce"},
                json={"auth_code": code},
                headers={
                    "apikey": supabase_anon_key,
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.error("Supabase token exchange failed: %s", exc)
            return JsonResponse(
                {"error": "Authentication service unavailable."},
                status=503,
            )

        if token_resp.status_code != 200:
            logger.warning(
                "Supabase token exchange returned %s", token_resp.status_code
            )
            return JsonResponse(
                {"error": "OAuth token exchange failed."},
                status=400,
            )

        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token", "")
        token_type = token_data.get("token_type", "")
        user_data = token_data.get("user") or {}

        # Validate token_type against allowlist (security: reject unexpected values).
        if token_type.lower() not in ALLOWED_TOKEN_TYPES and token_type.lower() != "bearer":
            logger.warning("Unexpected token_type from Supabase: %r", token_type)
            return JsonResponse(
                {"error": "OAuth token exchange failed."},
                status=400,
            )

        # ------------------------------------------------------------------
        # Step 2: Upsert users row via Supabase Admin REST API
        # ------------------------------------------------------------------
        user_id = user_data.get("id")
        email = user_data.get("email", "")
        full_name = (user_data.get("user_metadata") or {}).get("full_name", "")
        avatar_url = (user_data.get("user_metadata") or {}).get("avatar_url", "")

        if user_id:
            upsert_endpoint = f"{supabase_url}/rest/v1/users"
            upsert_payload = {
                "id": user_id,
                "email": email,
                "full_name": full_name,
                "avatar_url": avatar_url,
                "role": "player",
            }
            try:
                requests.post(
                    upsert_endpoint,
                    json=upsert_payload,
                    headers={
                        "apikey": supabase_service_key,
                        "Authorization": f"Bearer {supabase_service_key}",
                        "Content-Type": "application/json",
                        # Prefer=resolution=merge-duplicates instructs PostgREST to
                        # do an upsert (INSERT ... ON CONFLICT DO UPDATE).
                        "Prefer": "resolution=merge-duplicates",
                    },
                    timeout=10,
                )
            except requests.RequestException as exc:
                logger.error("Supabase users upsert failed: %s", exc)
                return JsonResponse(
                    {"error": "User profile service unavailable."},
                    status=503,
                )

        # ------------------------------------------------------------------
        # Step 3: Redirect to frontend with tokens in URL fragment
        # Tokens go in the fragment (#) — NOT in query params (?) — so they
        # are never sent to the server and don't appear in Referer headers.
        # ------------------------------------------------------------------
        fragment = urlencode({
            "access_token": access_token,
            "refresh_token": refresh_token,
        })
        redirect_url = f"{frontend_url}#{fragment}"
        return HttpResponseRedirect(redirect_url)


# ---------------------------------------------------------------------------
# Player auth
# ---------------------------------------------------------------------------

def _supabase_token_request(email: str, password: str) -> requests.Response:
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
    return requests.post(
        f"{supabase_url}/auth/v1/token",
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers={"apikey": supabase_anon_key, "Content-Type": "application/json"},
        timeout=10,
    )


@method_decorator(csrf_exempt, name="dispatch")
class PlayerLoginView(View):
    """POST /auth/player/login — generic 401 for all 4xx (anti-enumeration)."""

    _INVALID_CREDENTIALS_BODY = {"error": "invalid_credentials", "detail": "Invalid credentials"}

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        email = body.get("email")
        password = body.get("password")
        if not email:
            return JsonResponse({"error": "email is required."}, status=400)
        if not password:
            return JsonResponse({"error": "password is required."}, status=400)

        try:
            supabase_resp = _supabase_token_request(email, password)
        except requests.RequestException:
            return JsonResponse({"error": "Authentication service unavailable."}, status=503)

        if supabase_resp.status_code == 200:
            data = supabase_resp.json()
            user = data.get("user") or {}
            if not user.get("email_confirmed_at"):
                return JsonResponse({"error": "email_not_verified"}, status=403)
            return JsonResponse(
                {"access_token": data.get("access_token"), "refresh_token": data.get("refresh_token"), "user": user},
                status=200,
            )

        if 400 <= supabase_resp.status_code < 500:
            return JsonResponse(self._INVALID_CREDENTIALS_BODY, status=401)

        return JsonResponse({"error": "Authentication service unavailable."}, status=503)


@method_decorator(csrf_exempt, name="dispatch")
class PlayerForgotPasswordView(View):
    """POST /auth/player/forgot-password — always 200 (anti-enumeration)."""

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        email = body.get("email")
        if not email:
            return JsonResponse({"error": "email is required."}, status=400)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
        app_base_url = getattr(settings, "APP_BASE_URL", "")

        try:
            requests.post(
                f"{supabase_url}/auth/v1/recover",
                json={"email": email, "redirect_to": f"{app_base_url}/auth/callback?type=recovery"},
                headers={"apikey": supabase_anon_key, "Content-Type": "application/json"},
                timeout=10,
            )
        except requests.RequestException:
            pass

        return JsonResponse({"message": _RESET_LINK_SENT_MSG}, status=200)


_RESEND_RATE_LIMIT_SECONDS = 60


@method_decorator(csrf_exempt, name="dispatch")
class PlayerResendVerificationView(View):
    """POST /auth/player/resend-verification — rate limit 1/min per email."""

    def post(self, request):
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        email = body.get("email", "").strip().lower()
        if not email:
            return JsonResponse({"error": "validation_error", "detail": "email is required"}, status=400)

        cache_key = f"resend_verification:{email}"
        last_sent = cache.get(cache_key)
        if last_sent is not None:
            remaining = _RESEND_RATE_LIMIT_SECONDS - (time.time() - last_sent)
            if remaining > 0:
                return JsonResponse({"error": "rate_limited", "retry_after": math.ceil(remaining)}, status=429)

        cache.set(cache_key, time.time(), timeout=_RESEND_RATE_LIMIT_SECONDS)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
        app_base_url = getattr(settings, "APP_BASE_URL", "")

        try:
            requests.post(
                f"{supabase_url}/auth/v1/resend",
                json={"type": "signup", "email": email, "redirect_to": f"{app_base_url}/auth/callback?type=email"},
                headers={"apikey": supabase_anon_key, "Content-Type": "application/json"},
                timeout=10,
            )
        except requests.RequestException:
            pass

        return JsonResponse({"message": "Verification email sent"}, status=200)


class PlayerGoogleOAuthView(View):
    """Handle GET /auth/player/google by redirecting to Supabase Google OAuth."""

    def get(self, request):
        supabase_url = getattr(settings, "SUPABASE_URL", "")

        if not supabase_url:
            return JsonResponse(
                {"error": "Authentication service is unavailable."},
                status=503,
            )

        # Build the callback URL this app will handle after OAuth completes.
        # Client-supplied redirect_to is intentionally ignored to prevent open
        # redirect attacks; post-auth routing is handled by the callback view.
        callback_url = request.build_absolute_uri("/auth/callback")

        params = urlencode({
            "provider": "google",
            "redirect_to": callback_url,
        })
        supabase_oauth_url = f"{supabase_url}/auth/v1/authorize?{params}"

        return HttpResponseRedirect(supabase_oauth_url)
