"""
auth_ext.views — Authentication views for court owners.

POST /auth/owner/login
    Accepts {"email": "...", "password": "..."}
    Proxies to Supabase Auth signInWithPassword (POST /auth/v1/token?grant_type=password)
    Validates that the authenticated user has role='owner' in the users table.
    Returns {"access_token": "...", "refresh_token": "...", "user": {...}}
    Pure JWT flow — no Django session/cookie auth.
"""
import json

import requests
from django.conf import settings
from django.http import JsonResponse
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

        # Supabase returned an error — map to 401 for credential failures
        try:
            error_data = supabase_resp.json()
        except ValueError:
            error_data = {"error": "Unknown authentication error."}

        status_code = 401 if supabase_resp.status_code in (400, 401, 422) else 502
        return JsonResponse(
            {"error": error_data.get("error_description") or error_data.get("error") or "Authentication failed."},
            status=status_code,
        )
