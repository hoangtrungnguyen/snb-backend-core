"""
players.views — Player profile views.

GET /api/players/me
    Returns the current authenticated player's profile from public.users.
    Requires: SupabaseJWTAuthentication + IsPlayer permission.
    Returns 404 if user not found, 503 on network error.

PATCH /api/players/me
    Updates the player's full_name in public.users.

POST /api/players/me/fcm-token
    Registers a Firebase Cloud Messaging device token for push notifications.
    Accepts: {"token": "<non-empty string>"}
    Appends the token to public.users.fcm_tokens[] (idempotent — no duplicates).
    Returns 200 on success.
    Returns 400 if body is invalid/missing token.
    Returns 503 on network error.

DELETE /api/players/me/fcm-token
    Removes a Firebase Cloud Messaging device token (e.g. on logout).
    Accepts: {"token": "<non-empty string>"}
    Removes the token from public.users.fcm_tokens[].
    Returns 204 No Content on success.
    Returns 400 if body is invalid/missing token.
    Returns 503 on network error.
    Accepts: {"full_name": "<non-empty string>"}
    Returns 200 with updated profile on success.
    Returns 400 if body is invalid/missing full_name.
    Returns 404 if user not found.
    Returns 503 on network error.

POST /api/players/me/avatar
    Uploads a JPEG or PNG avatar (max 2 MB) to Supabase Storage and updates
    public.users.avatar_url.
    Accepts: multipart/form-data with field "avatar".
    Returns 200 with {"avatar_url": "<url>"} on success.
    Returns 400 for missing file, oversized file, or wrong MIME type.
    Returns 503 on network error.
"""
import json

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from rest_framework.exceptions import AuthenticationFailed

from players.validators import validate_avatar_file

_CONTENT_TYPE_TO_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
}

_AVATAR_BUCKET = "avatars"


def _authenticate_request(request):
    """
    Authenticate the request using the shared Supabase JWT decoder.

    Extracts and decodes the Bearer token; role is read from the JWT
    ``app_metadata.role`` claim (same as the middleware).

    Returns (SupabaseUser, token) on success, or None when no token is
    present.  Raises AuthenticationFailed on invalid/expired tokens.
    """
    from auth_ext.middleware import _decode_token
    from auth_ext.authentication import SupabaseUser

    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[len("Bearer "):]
    if not token:
        return None

    payload = _decode_token(token)
    if payload is None:
        raise AuthenticationFailed("Invalid or expired token.")

    uid = payload.get("sub")
    if not uid:
        raise AuthenticationFailed("Token is missing the 'sub' claim.")

    app_metadata = payload.get("app_metadata") or {}
    role = app_metadata.get("role") or "authenticated"

    return SupabaseUser(uid=uid, role=role), token


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

        if user.role != "player":
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

        if not isinstance(body, dict):
            return JsonResponse({"error": "Invalid request body."}, status=400)

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


@method_decorator(csrf_exempt, name="dispatch")
class PlayersFcmTokenView(View):
    """
    POST /api/players/me/fcm-token  — register a device token.
    DELETE /api/players/me/fcm-token — deregister a device token.
    """

    def _authenticate_player(self, request):
        """
        Authenticate + authorise the request as a player.

        Returns (user, None) on success or (None, JsonResponse) on failure.
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

        if user.role != "player":
            return None, JsonResponse(
                {"error": "You do not have permission to perform this action."}, status=403
            )

        return user, None

    def _parse_token_body(self, request):
        """
        Parse and validate the request body for a ``token`` field.

        Returns (token_value, None) on success or (None, JsonResponse) on error.
        """
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return None, JsonResponse({"error": "Invalid JSON body."}, status=400)

        if not isinstance(body, dict):
            return None, JsonResponse({"error": "Invalid request body."}, status=400)

        token_value = body.get("token")
        if token_value is None:
            return None, JsonResponse({"error": "token is required."}, status=400)
        if not isinstance(token_value, str):
            return None, JsonResponse({"error": "token must be a string."}, status=400)
        if not token_value.strip():
            return None, JsonResponse({"error": "token must not be empty."}, status=400)

        return token_value.strip(), None

    def _call_rpc(self, supabase_url, service_role_key, rpc_name, params):
        """
        Call a Supabase RPC function via POST /rest/v1/rpc/<name>.

        Returns the requests.Response object, or raises requests.RequestException.
        """
        rpc_url = f"{supabase_url}/rest/v1/rpc/{rpc_name}"
        return requests.post(
            rpc_url,
            json=params,
            headers=_supabase_headers(service_role_key),
            timeout=10,
        )

    def post(self, request):
        """Register a FCM device token for the authenticated player."""
        user, err_response = self._authenticate_player(request)
        if err_response is not None:
            return err_response

        fcm_token, err_response = self._parse_token_body(request)
        if err_response is not None:
            return err_response

        supabase_url, service_role_key = _get_supabase_keys()

        try:
            resp = self._call_rpc(
                supabase_url,
                service_role_key,
                "register_fcm_token",
                {"p_user_id": user.id, "p_token": fcm_token},
            )
        except requests.RequestException:
            return JsonResponse({"error": "FCM token service unavailable."}, status=503)

        if resp.status_code not in (200, 204):
            return JsonResponse({"error": "FCM token service unavailable."}, status=503)

        return JsonResponse({}, status=200)

    def delete(self, request):
        """Deregister a FCM device token for the authenticated player."""
        user, err_response = self._authenticate_player(request)
        if err_response is not None:
            return err_response

        fcm_token, err_response = self._parse_token_body(request)
        if err_response is not None:
            return err_response

        supabase_url, service_role_key = _get_supabase_keys()

        try:
            resp = self._call_rpc(
                supabase_url,
                service_role_key,
                "deregister_fcm_token",
                {"p_user_id": user.id, "p_token": fcm_token},
            )
        except requests.RequestException:
            return JsonResponse({"error": "FCM token service unavailable."}, status=503)

        if resp.status_code not in (200, 204):
            return JsonResponse({"error": "FCM token service unavailable."}, status=503)

        return HttpResponse(status=204)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class PlayersMeAvatarView(View):
    """POST /api/players/me/avatar — upload avatar to Supabase Storage."""

    def post(self, request):
        try:
            auth_result = _authenticate_request(request)
        except AuthenticationFailed as exc:
            return JsonResponse({"error": str(exc.detail)}, status=401)

        if auth_result is None:
            return JsonResponse(
                {"error": "Authentication credentials were not provided."}, status=401
            )

        user, _token = auth_result

        if user.role != "player":
            return JsonResponse(
                {"error": "You do not have permission to perform this action."}, status=403
            )

        avatar = request.FILES.get("avatar")
        if avatar is None:
            return JsonResponse({"error": "avatar file is required."}, status=400)

        try:
            validate_avatar_file(avatar)
        except ValidationError as exc:
            return JsonResponse({"error": exc.messages[0]}, status=400)

        ext = _CONTENT_TYPE_TO_EXT.get(avatar.content_type, "jpg")
        storage_path = f"{user.id}/avatar.{ext}"
        supabase_url, service_role_key = _get_supabase_keys()
        upload_url = f"{supabase_url}/storage/v1/object/{_AVATAR_BUCKET}/{storage_path}"

        try:
            upload_resp = requests.post(
                upload_url,
                data=avatar.read(),
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                    "Content-Type": avatar.content_type,
                    "x-upsert": "true",
                },
                timeout=30,
            )
        except requests.RequestException:
            return JsonResponse({"error": "Avatar upload service unavailable."}, status=503)

        if upload_resp.status_code not in (200, 201):
            return JsonResponse({"error": "Avatar upload failed."}, status=503)

        avatar_url = (
            f"{supabase_url}/storage/v1/object/public/{_AVATAR_BUCKET}/{storage_path}"
        )

        users_endpoint = f"{supabase_url}/rest/v1/users"
        try:
            patch_resp = requests.patch(
                users_endpoint,
                params={"id": f"eq.{user.id}"},
                json={"avatar_url": avatar_url},
                headers={
                    "apikey": service_role_key,
                    "Authorization": f"Bearer {service_role_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                timeout=10,
            )
        except requests.RequestException:
            return JsonResponse({"error": "Player profile service unavailable."}, status=503)

        if patch_resp.status_code not in (200, 204):
            return JsonResponse({"error": "Player profile service unavailable."}, status=503)

        return JsonResponse({"avatar_url": avatar_url}, status=200)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({"error": "Method not allowed."}, status=405)
