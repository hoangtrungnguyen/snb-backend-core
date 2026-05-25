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

import requests
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

            # Validate that the authenticated user has role='owner' in the users table.
            # This check must happen BEFORE returning tokens to the caller.
            user_id = (data.get("user") or {}).get("id", "")
            role_check_result = self._check_owner_role(user_id)
            if role_check_result is not None:
                return role_check_result

            return JsonResponse(
                {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "user": data.get("user"),
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
            return JsonResponse(
                {"error": "Authentication service unavailable.", "detail": str(exc)},
                status=502,
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

        # Supabase 422 means the email is already registered
        if supabase_resp.status_code == 422:
            return JsonResponse(
                {"error": "email_already_registered"},
                status=409,
            )

        # Other Supabase errors
        try:
            error_data = supabase_resp.json()
        except ValueError:
            error_data = {"error": "Unknown error from authentication service."}

        return JsonResponse(
            {"error": error_data.get("msg") or error_data.get("error") or "Signup failed."},
            status=502,
        )


@method_decorator(csrf_exempt, name="dispatch")
class AuthCallbackView(View):
    """
    Handle GET /auth/callback — the Supabase email-verification redirect target.

    Two supported flows:
      - PKCE:       ?code=<code>
      - token_hash: ?token_hash=<hash>&type=<type>

    On success, redirects to FRONTEND_URL with tokens in URL fragment.
    On failure returns 400 {"error": "verification_failed"}.
    """

    _VERIFICATION_FAILED = {"error": "verification_failed"}
    ALLOWED_TOKEN_TYPES = {"email", "signup", "recovery", "invite"}

    def get(self, request):
        code = request.GET.get("code")
        token_hash = request.GET.get("token_hash")
        token_type = request.GET.get("type")

        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
        headers = {"apikey": supabase_anon_key, "Content-Type": "application/json"}

        try:
            if code:
                supabase_resp = requests.post(
                    f"{supabase_url}/auth/v1/token",
                    params={"grant_type": "pkce"},
                    json={"auth_code": code},
                    headers=headers,
                    timeout=10,
                )
            elif token_hash and token_type:
                if token_type not in self.ALLOWED_TOKEN_TYPES:
                    return JsonResponse(
                        {"error": "verification_failed", "detail": "Invalid token type"},
                        status=400,
                    )
                supabase_resp = requests.post(
                    f"{supabase_url}/auth/v1/verify",
                    json={"token_hash": token_hash, "type": token_type},
                    headers=headers,
                    timeout=10,
                )
            else:
                return JsonResponse(self._VERIFICATION_FAILED, status=400)
        except requests.RequestException:
            return JsonResponse(self._VERIFICATION_FAILED, status=400)

        if supabase_resp.status_code != 200:
            return JsonResponse(self._VERIFICATION_FAILED, status=400)

        data = supabase_resp.json()
        access_token = data.get("access_token", "")
        refresh_token = data.get("refresh_token", "")

        frontend_url = getattr(settings, "FRONTEND_URL", "")
        if frontend_url:
            fragment = urlencode({"access_token": access_token, "refresh_token": refresh_token})
            return HttpResponseRedirect(f"{frontend_url}#{fragment}")

        return JsonResponse(
            {"status": "verified", "access_token": access_token, "refresh_token": refresh_token},
            status=200,
        )


# ---------------------------------------------------------------------------
# Player auth
# ---------------------------------------------------------------------------

def _supabase_token_request(email: str, password: str) -> requests.Response:
    """
    Make a signInWithPassword request to the Supabase Auth REST API.

    Returns the raw requests.Response.
    Raises requests.RequestException on network failure.
    """
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
    token_endpoint = f"{supabase_url}/auth/v1/token"

    return requests.post(
        token_endpoint,
        params={"grant_type": "password"},
        json={"email": email, "password": password},
        headers={
            "apikey": supabase_anon_key,
            "Content-Type": "application/json",
        },
        timeout=10,
    )


@method_decorator(csrf_exempt, name="dispatch")
class PlayerLoginView(View):
    """Handle POST /auth/player/login by delegating to Supabase Auth.

    Differences from OwnerLoginView:
    - Does NOT check for owner role — any role is accepted.
    - Returns generic 401 {"error": "invalid_credentials"} on bad credentials
      (no enumeration of whether email or password was wrong).
    - Still checks email_confirmed_at IS NOT NULL → 403 {"error": "email_not_verified"}.
    """

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

        try:
            supabase_resp = _supabase_token_request(email, password)
        except requests.RequestException as exc:
            return JsonResponse(
                {"error": "Authentication service unavailable.", "detail": str(exc)},
                status=502,
            )

        if supabase_resp.status_code == 200:
            data = supabase_resp.json()
            user = data.get("user") or {}

            # Enforce email verification: return 403 if email_confirmed_at is null.
            if not user.get("email_confirmed_at"):
                return JsonResponse(
                    {"error": "email_not_verified"},
                    status=403,
                )

            return JsonResponse(
                {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                    "user": user,
                },
                status=200,
            )

        # Supabase returned an error — always 401 with generic message (no enumeration).
        return JsonResponse(
            {"error": "invalid_credentials"},
            status=401,
        )
