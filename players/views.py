"""
players.views — Player profile views.

GET /api/players/me
    Returns the current authenticated player's profile from public.users.
    Requires: SupabaseJWTAuthentication + IsPlayer permission.
    Returns 404 if user not found, 503 on network error.

PATCH /api/players/me
    Updates the player's full_name in public.users.
    Accepts: {"full_name": "<non-empty string>"}
    Returns 200 with updated profile on success.
    Returns 400 if body is invalid/missing full_name.
    Returns 404 if user not found.
    Returns 503 on network error.
"""
import json

import requests
from django.conf import settings
from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import AuthenticationFailed

from auth_ext.authentication import SupabaseJWTAuthentication


def _authenticate_request(request):
    """
    Authenticate the request using SupabaseJWTAuthentication.

    Returns (user, None) on success, or raises an exception on failure.
    """
    authenticator = SupabaseJWTAuthentication()
    result = authenticator.authenticate(request)
    return result


def _get_supabase_keys():
    """Return (supabase_url, service_role_key) from settings."""
    supabase_url = getattr(settings, "SUPABASE_URL", "")
    supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
    service_role_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or supabase_anon_key
    return supabase_url, service_role_key


def _supabase_headers(service_role_key):
    """Return common headers for Supabase REST API calls."""
    return {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation",
    }


@method_decorator(csrf_exempt, name="dispatch")
class PlayersMeView(View):
    """
    GET /api/players/me  — returns the current player's profile.
    PATCH /api/players/me — updates the player's full_name.
    """

    def _authenticate(self, request):
        """
        Authenticate + authorise the request.

        Returns (user, None) on success or a JsonResponse on failure.
        """
        try:
            auth_result = _authenticate_request(request)
        except AuthenticationFailed as exc:
            return None, JsonResponse({"error": str(exc.detail)}, status=401)

        if auth_result is None:
            return None, JsonResponse(
                {"error": "Authentication credentials were not provided."}, status=401
            )

        user, _token = auth_result

        if user.player_role != "player":
            return None, JsonResponse(
                {"error": "You do not have permission to perform this action."}, status=403
            )

        return user, None

    def get(self, request):
        user, err_response = self._authenticate(request)
        if err_response is not None:
            return err_response

        supabase_url, service_role_key = _get_supabase_keys()
        users_endpoint = f"{supabase_url}/rest/v1/users"

        try:
            resp = requests.get(
                users_endpoint,
                params={
                    "id": f"eq.{user.id}",
                    "select": "id,email,name,phone,role",
                    "limit": "1",
                },
                headers=_supabase_headers(service_role_key),
                timeout=10,
            )
        except requests.RequestException:
            return JsonResponse({"error": "Player profile service unavailable."}, status=503)

        if resp.status_code != 200:
            return JsonResponse({"error": "Player profile service unavailable."}, status=503)

        data = resp.json()
        if not data:
            return JsonResponse({"error": "Player profile not found."}, status=404)

        profile = data[0]
        return JsonResponse(
            {
                "id": profile.get("id"),
                "email": profile.get("email"),
                "name": profile.get("name"),
                "phone": profile.get("phone"),
                "role": profile.get("role"),
            },
            status=200,
        )

    def patch(self, request):
        user, err_response = self._authenticate(request)
        if err_response is not None:
            return err_response

        # Parse body
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON body."}, status=400)

        # Validate full_name
        full_name = body.get("full_name")
        if full_name is None:
            return JsonResponse({"error": "full_name is required."}, status=400)
        if not isinstance(full_name, str):
            return JsonResponse({"error": "full_name must be a string."}, status=400)
        if not full_name.strip():
            return JsonResponse({"error": "full_name must not be empty."}, status=400)

        supabase_url, service_role_key = _get_supabase_keys()
        users_endpoint = f"{supabase_url}/rest/v1/users"

        try:
            resp = requests.patch(
                users_endpoint,
                params={
                    "id": f"eq.{user.id}",
                    "select": "id,email,name,phone,role",
                },
                json={"name": full_name.strip()},
                headers=_supabase_headers(service_role_key),
                timeout=10,
            )
        except requests.RequestException:
            return JsonResponse({"error": "Player profile service unavailable."}, status=503)

        if resp.status_code != 200:
            return JsonResponse({"error": "Player profile service unavailable."}, status=503)

        data = resp.json()
        if not data:
            return JsonResponse({"error": "Player profile not found."}, status=404)

        profile = data[0]
        return JsonResponse(
            {
                "id": profile.get("id"),
                "email": profile.get("email"),
                "name": profile.get("name"),
                "phone": profile.get("phone"),
                "role": profile.get("role"),
            },
            status=200,
        )

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)
