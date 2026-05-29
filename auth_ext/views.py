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
    Uses SUPABASE_PUBLISHABLE_KEY (not service role key).

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
from supabase_auth.errors import AuthApiError

from auth_ext.supabase_client import get_admin_client, get_anon_client
from django.core.cache import cache
from django.conf import settings
from django.http import JsonResponse, HttpResponseRedirect
import urllib.parse
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
        service_role_key = getattr(settings, "SUPABASE_SECRET_KEY", "")

        users_endpoint = f"{supabase_url}/rest/v1/customers"

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
        supabase_anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")

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
        supabase_anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")

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
        supabase_anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")

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
            "SUPABASE_SECRET_KEY not set; cannot check identity provider for email=%s",
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

        try:
            auth_resp = get_anon_client().auth.sign_up(
                {"email": email, "password": password}
            )
        except AuthApiError as exc:
            if exc.status == 422:
                supabase_url = getattr(settings, "SUPABASE_URL", "")
                service_role_key = getattr(settings, "SUPABASE_SECRET_KEY", "")
                if _check_google_oauth_provider(supabase_url, service_role_key, email):
                    return JsonResponse(
                        {"code": "account_exists_other_provider"},
                        status=409,
                    )
                return JsonResponse(
                    {"error": "email_already_registered"},
                    status=409,
                )
            logger.error("Supabase signup error %s: %s", exc.status, exc.message)
            return JsonResponse({"error": "Signup failed."}, status=503)
        except Exception as exc:  # transport / unexpected
            logger.error("Supabase signup transport error: %s", exc)
            return JsonResponse(
                {"error": "Authentication service unavailable."},
                status=502,
            )

        user = auth_resp.user
        return JsonResponse(
            {
                "message": "Confirmation email sent",
                "user": {
                    "id": getattr(user, "id", None),
                    "email": getattr(user, "email", email),
                },
            },
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class OwnerSignupView(View):
    """Handle POST /auth/owner/signup.

    Creates an auto-confirmed Supabase auth user via the admin API and
    promotes the corresponding `customers` row from the trigger-inserted
    default role ``player`` to ``owner``.

    Body: {"email": "...", "password": "..."}
    Returns:
        201 {"message": "Owner account created", "user": {"id", "email"}}
        400 on invalid body, missing fields, or password rule violations
        409 {"error": "email_already_registered"} when Supabase returns 422
        502/503 on upstream auth/profile service failure
    """

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

        validation_errors = _validate_password(password)
        if validation_errors:
            return JsonResponse(
                {"error": "validation_error", "detail": validation_errors},
                status=400,
            )

        admin = get_admin_client()

        try:
            created = admin.auth.admin.create_user(
                {"email": email, "password": password, "email_confirm": True}
            )
        except AuthApiError as exc:
            if exc.status == 422:
                return JsonResponse({"error": "email_already_registered"}, status=409)
            logger.error(
                "Supabase admin create_user error %s: %s", exc.status, exc.message
            )
            return JsonResponse({"error": "Signup failed."}, status=503)
        except Exception as exc:
            logger.error("Supabase admin create_user transport error: %s", exc)
            return JsonResponse({"error": "Authentication service unavailable."}, status=502)

        user = created.user
        user_id = getattr(user, "id", None)
        user_email = getattr(user, "email", email)

        # Promote the customers row inserted by the handle_new_user trigger
        # from default role='player' to 'owner'.
        if user_id:
            try:
                admin.table("customers").update({"role": "owner"}).eq(
                    "id", user_id
                ).execute()
            except Exception as exc:
                logger.error("customers role update failed: %s", exc)
                return JsonResponse(
                    {"error": "User profile service unavailable."}, status=503
                )

        return JsonResponse(
            {"message": "Owner account created", "user": {"id": user_id, "email": user_email}},
            status=201,
        )


@method_decorator(csrf_exempt, name="dispatch")
class AuthCallbackView(View):
    """Handle GET /auth/callback — OAuth redirect handler with identity merge.

    Receives the authorization code from Supabase after an OAuth flow (e.g.
    Google sign-in), exchanges it for access+refresh tokens via the Supabase
    PKCE token endpoint, then upserts or merges a `users` row:

    - New user or same-provider re-login: INSERT ... ON CONFLICT DO UPDATE (upsert).
    - Cross-provider merge (same email, different UID): PATCH the existing row
      preserving the original users.id by PATCHing that existing row instead of
      inserting a new one.
    """

    def get(self, request):
        code = request.GET.get("code", "").strip()
        if not code:
            return JsonResponse({"error": "Missing required parameter: code."}, status=400)

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")
        supabase_service_key = getattr(settings, "SUPABASE_SECRET_KEY", "")
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
        # With identity merge: if a row with the same email already exists
        # under a different UID (e.g. prior email/password signup), preserve
        # the original UID by updating that row instead of inserting a new one.
        # ------------------------------------------------------------------
        user_id = user_data.get("id")
        email = user_data.get("email", "")
        full_name = (user_data.get("user_metadata") or {}).get("full_name", "")
        avatar_url = (user_data.get("user_metadata") or {}).get("avatar_url", "")

        if user_id:
            admin_headers = {
                "apikey": supabase_service_key,
                "Authorization": f"Bearer {supabase_service_key}",
                "Content-Type": "application/json",
            }
            users_endpoint = f"{supabase_url}/rest/v1/customers"

            # ------------------------------------------------------------------
            # Step 2a: Look up existing users row by email to detect merge case.
            # ------------------------------------------------------------------
            existing_uid = None
            if email:
                encoded_email = urllib.parse.quote(email, safe="")
                lookup_endpoint = f"{users_endpoint}?email=eq.{encoded_email}&select=id,email"
                try:
                    lookup_resp = requests.get(
                        lookup_endpoint,
                        headers=admin_headers,
                        timeout=10,
                    )
                except requests.RequestException as exc:
                    logger.error("Supabase users lookup failed: %s", exc)
                    return JsonResponse(
                        {"error": "User profile service unavailable."},
                        status=503,
                    )

                if lookup_resp.status_code != 200:
                    logger.error(
                        "Supabase users lookup returned %s: %s",
                        lookup_resp.status_code,
                        lookup_resp.text,
                    )
                    return JsonResponse(
                        {"error": "User profile service unavailable."},
                        status=503,
                    )

                try:
                    rows = lookup_resp.json()
                    if rows and isinstance(rows, list):
                        existing_uid = rows[0].get("id")
                except (ValueError, AttributeError):
                    existing_uid = None

            # ------------------------------------------------------------------
            # Step 2b: Merge or upsert.
            # Merge: existing row with a DIFFERENT UID found → update that row,
            #        preserving the original users.id (the key invariant).
            # No merge: same UID or no existing row → normal upsert.
            # ------------------------------------------------------------------
            if existing_uid and existing_uid != user_id:
                # Identity merge: user signed up with email/password before,
                # now signing in with Google using the same email.
                # Preserve the original UID by updating the existing row.
                logger.info(
                    "Merging identities: email=%s original_uid=%s new_uid=%s",
                    email,
                    existing_uid,
                    user_id,
                )
                merge_endpoint = f"{users_endpoint}?id=eq.{existing_uid}"
                merge_payload = {
                    "full_name": full_name,
                    "avatar_url": avatar_url,
                    "role": "player",
                }
                try:
                    patch_resp = requests.patch(
                        merge_endpoint,
                        json=merge_payload,
                        headers={
                            **admin_headers,
                            "Prefer": "return=minimal",
                        },
                        timeout=10,
                    )
                except requests.RequestException as exc:
                    logger.error("Supabase users merge update failed: %s", exc)
                    return JsonResponse(
                        {"error": "User profile service unavailable."},
                        status=503,
                    )
                if not (200 <= patch_resp.status_code < 300):
                    logger.error(
                        "Supabase users merge update returned %s: %s",
                        patch_resp.status_code,
                        patch_resp.text,
                    )
                    return JsonResponse(
                        {"error": "User profile service unavailable."},
                        status=503,
                    )
            else:
                # Normal upsert: either new user or same provider re-login.
                upsert_payload = {
                    "id": user_id,
                    "email": email,
                    "full_name": full_name,
                    "avatar_url": avatar_url,
                    "role": "player",
                }
                try:
                    requests.post(
                        users_endpoint,
                        json=upsert_payload,
                        headers={
                            **admin_headers,
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
    supabase_anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")
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
        supabase_anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")
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
        supabase_anon_key = getattr(settings, "SUPABASE_PUBLISHABLE_KEY", "")
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
