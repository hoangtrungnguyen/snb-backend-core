"""
players.views — Player profile views.

GET /api/players/me
    Returns the current authenticated player's profile from public.users.
    Requires: SupabaseJWTAuthentication + IsPlayer permission.
    Returns 404 if user not found, 503 on network error.
"""
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


@method_decorator(csrf_exempt, name="dispatch")
class PlayersMeView(View):
    """
    GET /api/players/me

    Returns the current player's profile from public.users.
    """

    def get(self, request):
        # Authenticate
        try:
            auth_result = _authenticate_request(request)
        except AuthenticationFailed as exc:
            return JsonResponse({"error": str(exc.detail)}, status=401)

        if auth_result is None:
            return JsonResponse({"error": "Authentication credentials were not provided."}, status=401)

        user, _token = auth_result

        # Enforce IsPlayer permission
        if user.role != "player":
            return JsonResponse({"error": "You do not have permission to perform this action."}, status=403)

        # Fetch from Supabase public.users
        supabase_url = getattr(settings, "SUPABASE_URL", "")
        supabase_anon_key = getattr(settings, "SUPABASE_ANON_KEY", "")
        service_role_key = getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or supabase_anon_key

        users_endpoint = f"{supabase_url}/rest/v1/users"

        try:
            resp = requests.get(
                users_endpoint,
                params={
                    "id": f"eq.{user.id}",
                    "select": "id,email,name,phone,role",
                    "limit": "1",
                },
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
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
