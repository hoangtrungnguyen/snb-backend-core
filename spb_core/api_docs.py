"""
spb_core.api_docs — OpenAPI documentation layer.

The live endpoints in ``auth_ext`` and ``players`` are plain
``django.views.View`` instances (manual JWT auth + ``JsonResponse``), which
drf-spectacular cannot introspect. Rather than rewrite those battle-tested
views, this module mirrors each one as a lightweight ``@api_view`` *doc-stub*
decorated with ``@extend_schema``.

The ``urlpatterns`` below are wired ONLY into ``SpectacularAPIView(urlconf=...)``
in :mod:`spb_core.urls`; they never serve real traffic and never shadow the
real routes. The stub bodies are unreachable during schema generation — the
``raise NotImplementedError`` is purely defensive.

Keep this file in sync with the real views: the request/response serializers
and status codes here are the contract published to humans (Swagger/Redoc) and
AI agents (raw ``/api/schema/`` + ``/llms.txt``).
"""
from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import serializers
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from django.urls import path


# ---------------------------------------------------------------------------
# Shared serializers
# ---------------------------------------------------------------------------
class ErrorSerializer(serializers.Serializer):
    """Standard error envelope returned by every failing endpoint."""

    error = serializers.CharField(
        help_text="Machine-readable error code, e.g. `invalid_credentials`."
    )
    detail = serializers.CharField(
        required=False,
        help_text="Optional human-readable explanation of the error.",
    )


class MessageSerializer(serializers.Serializer):
    message = serializers.CharField(help_text="Human-readable status message.")


class CredentialsSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Account email address.")
    password = serializers.CharField(
        write_only=True,
        help_text="Account password. Min 8 chars, ≥1 letter, ≥1 digit on signup.",
    )


class EmailOnlySerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Account email address.")


class MinimalUserSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Supabase auth user id (UUID).")
    email = serializers.EmailField(help_text="User email address.")


class TokenPairSerializer(serializers.Serializer):
    """Successful login / refresh response — Supabase-issued JWTs."""

    access_token = serializers.CharField(help_text="Short-lived JWT access token.")
    refresh_token = serializers.CharField(
        help_text="Long-lived token used at `POST /auth/refresh`."
    )
    user = serializers.JSONField(
        help_text="Raw Supabase user object (id, email, app_metadata, …)."
    )


class SignupResultSerializer(serializers.Serializer):
    message = serializers.CharField(help_text="Status message.")
    user = MinimalUserSerializer()


class RefreshRequestSerializer(serializers.Serializer):
    refresh_token = serializers.CharField(
        help_text="A valid refresh token previously issued by login."
    )


class ResendVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField(help_text="Email to resend the verification link to.")


class RateLimitedSerializer(serializers.Serializer):
    error = serializers.CharField(help_text="Always `rate_limited`.")
    retry_after = serializers.IntegerField(
        help_text="Seconds to wait before retrying (rate limit: 1 request/minute)."
    )


class PlayerProfileSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Player user id (UUID).")
    email = serializers.EmailField(help_text="Player email address.")
    name = serializers.CharField(allow_null=True, help_text="Player display name.")
    phone = serializers.CharField(allow_null=True, help_text="Player phone number.")
    role = serializers.CharField(help_text="Always `player` for this endpoint.")


class UpdateNameSerializer(serializers.Serializer):
    full_name = serializers.CharField(
        max_length=255,
        help_text="New display name. Must be a non-empty string.",
    )


class FcmTokenSerializer(serializers.Serializer):
    token = serializers.CharField(
        help_text="Firebase Cloud Messaging device token. Non-empty string."
    )


class LocationSerializer(serializers.Serializer):
    lat = serializers.FloatField(
        min_value=-90.0, max_value=90.0, help_text="Latitude in degrees [-90, 90]."
    )
    lng = serializers.FloatField(
        min_value=-180.0, max_value=180.0, help_text="Longitude in degrees [-180, 180]."
    )


class LocationResultSerializer(serializers.Serializer):
    last_lat = serializers.FloatField(help_text="Stored latitude.")
    last_lng = serializers.FloatField(help_text="Stored longitude.")


class AvatarUploadSerializer(serializers.Serializer):
    avatar = serializers.ImageField(
        help_text="JPEG or PNG image, max 2 MB. Sent as multipart/form-data."
    )


class AvatarResultSerializer(serializers.Serializer):
    avatar_url = serializers.URLField(help_text="Public URL of the uploaded avatar.")


class HealthSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=["ok", "error"], help_text="Overall health: `ok` (200) or `error` (503)."
    )
    db = serializers.ChoiceField(choices=["ok", "error"], help_text="Database connectivity.")
    realtime = serializers.ChoiceField(
        choices=["ok", "error"], help_text="Supabase Realtime status."
    )


# Reusable error responses ---------------------------------------------------
_UNAUTHORIZED = OpenApiResponse(ErrorSerializer, description="Missing or invalid Bearer token.")
_FORBIDDEN_ROLE = OpenApiResponse(ErrorSerializer, description="Authenticated user lacks the required role.")
_NOT_FOUND = OpenApiResponse(ErrorSerializer, description="Resource not found.")
_BAD_JSON = OpenApiResponse(ErrorSerializer, description="Invalid JSON body or missing/invalid fields.")
_UPSTREAM = OpenApiResponse(ErrorSerializer, description="Upstream auth/profile service unavailable.")


def _stub(request, *args, **kwargs):  # pragma: no cover - never executed
    raise NotImplementedError("Documentation stub — not a live endpoint.")


# ===========================================================================
# Auth — Owner
# ===========================================================================
@extend_schema(
    tags=["Auth — Owner"],
    auth=[],
    summary="Owner signup",
    description=(
        "Create an auto-confirmed court-owner account via the Supabase admin API "
        "(`email_confirm=True`, `app_metadata.role=owner`), then promote the "
        "`customers` row to `owner`. No confirmation email is sent."
    ),
    request=CredentialsSerializer,
    responses={
        201: OpenApiResponse(SignupResultSerializer, description="Owner account created."),
        400: _BAD_JSON,
        409: OpenApiResponse(ErrorSerializer, description="`email_already_registered`."),
        502: _UPSTREAM,
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def owner_signup(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Owner"],
    auth=[],
    summary="Owner login",
    description=(
        "Authenticate a court owner against Supabase (`grant_type=password`). "
        "Requires a verified email and `role=owner` in the `customers` table. "
        "Errors are deliberately generic to prevent user enumeration."
    ),
    request=CredentialsSerializer,
    responses={
        200: OpenApiResponse(TokenPairSerializer, description="Login succeeded; tokens issued."),
        400: _BAD_JSON,
        401: OpenApiResponse(ErrorSerializer, description="`invalid_credentials`."),
        403: OpenApiResponse(
            ErrorSerializer,
            description="`email_not_verified` or `forbidden` (non-owner role).",
        ),
        502: _UPSTREAM,
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def owner_login(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Owner"],
    auth=[],
    summary="Owner forgot password",
    description=(
        "Trigger a Supabase password-reset email. Always returns 200 regardless of "
        "whether the email exists (anti-enumeration)."
    ),
    request=EmailOnlySerializer,
    responses={
        200: OpenApiResponse(MessageSerializer, description="Reset link sent if the email exists."),
        400: _BAD_JSON,
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def owner_forgot_password(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Owner"],
    auth=[],
    summary="Refresh access token",
    description="Exchange a valid refresh token for a new access/refresh token pair.",
    request=RefreshRequestSerializer,
    responses={
        200: OpenApiResponse(TokenPairSerializer, description="New token pair issued."),
        400: _BAD_JSON,
        401: OpenApiResponse(ErrorSerializer, description="`invalid_token` — expired or invalid."),
        502: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def token_refresh(request):
    return _stub(request)


# ===========================================================================
# Auth — Player
# ===========================================================================
@extend_schema(
    tags=["Auth — Player"],
    auth=[],
    summary="Player signup",
    description=(
        "Register a player via Supabase `signUp`. Sends a confirmation email. "
        "Password must be ≥8 chars with at least one letter and one digit."
    ),
    request=CredentialsSerializer,
    responses={
        201: OpenApiResponse(SignupResultSerializer, description="Confirmation email sent."),
        400: OpenApiResponse(ErrorSerializer, description="`validation_error` or missing fields."),
        409: OpenApiResponse(
            ErrorSerializer,
            description="`email_already_registered`, or `account_exists_other_provider` (Google).",
        ),
        502: _UPSTREAM,
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def player_signup(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Player"],
    auth=[],
    summary="Player login",
    description="Authenticate a player against Supabase. Requires a verified email.",
    request=CredentialsSerializer,
    responses={
        200: OpenApiResponse(TokenPairSerializer, description="Login succeeded; tokens issued."),
        400: _BAD_JSON,
        401: OpenApiResponse(ErrorSerializer, description="`invalid_credentials`."),
        403: OpenApiResponse(ErrorSerializer, description="`email_not_verified`."),
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def player_login(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Player"],
    auth=[],
    summary="Player forgot password",
    description="Trigger a Supabase password-reset email. Always 200 (anti-enumeration).",
    request=EmailOnlySerializer,
    responses={
        200: OpenApiResponse(MessageSerializer, description="Reset link sent if the email exists."),
        400: _BAD_JSON,
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def player_forgot_password(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Player"],
    auth=[],
    summary="Resend verification email",
    description="Resend the signup confirmation email. Rate limited to 1 request/minute per email.",
    request=ResendVerificationSerializer,
    responses={
        200: OpenApiResponse(MessageSerializer, description="Verification email sent."),
        400: OpenApiResponse(ErrorSerializer, description="`validation_error` — email required."),
        429: OpenApiResponse(RateLimitedSerializer, description="`rate_limited` — retry after `retry_after` seconds."),
    },
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def player_resend_verification(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Player"],
    auth=[],
    summary="OAuth callback (Google)",
    description=(
        "Handles the Supabase OAuth redirect: exchanges the `code` for tokens, "
        "upserts/merges the `customers` row, then 302-redirects to the frontend "
        "with tokens in the URL fragment."
    ),
    parameters=[
        OpenApiParameter(
            name="code",
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            required=True,
            description="Authorization code returned by Supabase OAuth.",
        )
    ],
    responses={
        302: OpenApiResponse(description="Redirect to the frontend with tokens in the URL fragment."),
        400: OpenApiResponse(ErrorSerializer, description="Missing `code` or token exchange failed."),
        503: _UPSTREAM,
    },
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def auth_callback(request):
    return _stub(request)


@extend_schema(
    tags=["Auth — Player"],
    auth=[],
    summary="Begin Google OAuth",
    description="302-redirects the browser to Supabase's Google OAuth authorize endpoint.",
    responses={
        302: OpenApiResponse(description="Redirect to the Supabase Google OAuth authorize URL."),
        503: OpenApiResponse(ErrorSerializer, description="Auth service not configured."),
    },
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def player_google_oauth(request):
    return _stub(request)


# ===========================================================================
# Players (authenticated; role=player)
# ===========================================================================
@extend_schema(
    tags=["Players"],
    methods=["GET"],
    summary="Get my profile",
    description="Return the authenticated player's profile from `public.customers`.",
    responses={
        200: OpenApiResponse(PlayerProfileSerializer, description="Player profile."),
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@extend_schema(
    tags=["Players"],
    methods=["PATCH"],
    summary="Update my profile",
    description="Update the player's `full_name` in `public.customers`.",
    request=UpdateNameSerializer,
    responses={
        200: OpenApiResponse(PlayerProfileSerializer, description="Updated player profile."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@api_view(["GET", "PATCH"])
@authentication_classes([])
def players_me(request):
    return _stub(request)


@extend_schema(
    tags=["Players"],
    methods=["POST"],
    summary="Register FCM device token",
    description="Append a Firebase Cloud Messaging device token (idempotent — no duplicates).",
    request=FcmTokenSerializer,
    responses={
        200: OpenApiResponse(description="Token registered."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        503: _UPSTREAM,
    },
)
@extend_schema(
    tags=["Players"],
    methods=["DELETE"],
    summary="Deregister FCM device token",
    description="Remove a Firebase Cloud Messaging device token (e.g. on logout).",
    request=FcmTokenSerializer,
    responses={
        204: OpenApiResponse(description="Token removed."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        503: _UPSTREAM,
    },
)
@api_view(["POST", "DELETE"])
@authentication_classes([])
def players_fcm_token(request):
    return _stub(request)


@extend_schema(
    tags=["Players"],
    summary="Update my location",
    description="Update the player's current location (`last_lat`/`last_lng`). No history stored.",
    request=LocationSerializer,
    responses={
        200: OpenApiResponse(LocationResultSerializer, description="Stored location."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@api_view(["PATCH"])
@authentication_classes([])
def players_me_location(request):
    return _stub(request)


@extend_schema(
    tags=["Players"],
    summary="Upload my avatar",
    description="Upload a JPEG or PNG avatar (max 2 MB) to Supabase Storage and update `avatar_url`.",
    request={"multipart/form-data": AvatarUploadSerializer},
    responses={
        200: OpenApiResponse(AvatarResultSerializer, description="Avatar uploaded."),
        400: OpenApiResponse(ErrorSerializer, description="Missing file, oversized, or wrong MIME type."),
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
def players_me_avatar(request):
    return _stub(request)


# ===========================================================================
# System
# ===========================================================================
@extend_schema(
    tags=["System"],
    auth=[],
    summary="Health check",
    description="Liveness/readiness probe. No authentication required.",
    responses={
        200: OpenApiResponse(HealthSerializer, description="All checks passed."),
        503: OpenApiResponse(HealthSerializer, description="One or more checks failed."),
    },
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    return _stub(request)


# ===========================================================================
# Courts / Slots / Bookings — shared serializers
# ===========================================================================
_SLOT_STATUS = ["open", "booked", "blocked", "maintenance"]
_ACCESS_POLICY = ["open", "private"]
_BOOKING_STATUS = ["pending", "confirmed", "cancelled", "completed"]


class CourtSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Court id (UUID).")
    name = serializers.CharField(help_text="Court display name.")
    slug = serializers.CharField(help_text="URL-safe slug (used for QR deep-links).")
    status = serializers.CharField(help_text="Lifecycle status, e.g. `approved`, `pending`.")
    sport_types = serializers.ListField(
        child=serializers.CharField(), help_text="Supported sports, e.g. `[\"tennis\"]`."
    )
    price_per_hour = serializers.FloatField(help_text="Base hourly price.")
    address = serializers.CharField(allow_null=True, required=False, help_text="Street address.")
    capacity = serializers.IntegerField(allow_null=True, required=False, help_text="Max players.")
    description = serializers.CharField(allow_null=True, required=False)
    amenities = serializers.ListField(child=serializers.CharField(), required=False)
    photos = serializers.ListField(child=serializers.URLField(), required=False)
    operating_hours = serializers.JSONField(required=False, help_text="Weekly opening hours object.")
    created_at = serializers.DateTimeField(required=False)


class CourtListSerializer(serializers.Serializer):
    count = serializers.IntegerField(required=False, help_text="Total matching courts.")
    results = CourtSerializer(many=True)


class CreateCourtSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="Court display name (required).")
    sport_types = serializers.ListField(
        child=serializers.CharField(), help_text="Supported sports."
    )
    price_per_hour = serializers.FloatField(min_value=0, help_text="Base hourly price.")
    address = serializers.CharField(required=False)
    capacity = serializers.IntegerField(required=False, min_value=1)
    description = serializers.CharField(required=False)
    amenities = serializers.ListField(child=serializers.CharField(), required=False)
    photos = serializers.ListField(child=serializers.URLField(), required=False)
    operating_hours = serializers.JSONField(required=False, help_text="Weekly opening hours object.")


class UpdateCourtSerializer(CreateCourtSerializer):
    """All fields optional — PATCH semantics."""

    name = serializers.CharField(required=False)
    sport_types = serializers.ListField(child=serializers.CharField(), required=False)
    price_per_hour = serializers.FloatField(required=False, min_value=0)


class CourtSettingsSerializer(serializers.Serializer):
    auto_approve_single = serializers.BooleanField(
        help_text="When true, single bookings are auto-confirmed instead of pending."
    )


class CourtSettingsResultSerializer(serializers.Serializer):
    court_id = serializers.UUIDField()
    auto_approve_single = serializers.BooleanField()


class SlotSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Slot id (UUID).")
    court_id = serializers.UUIDField()
    court_name = serializers.CharField(required=False, help_text="Denormalized court name.")
    start_at = serializers.DateTimeField(help_text="Slot start (ISO 8601).")
    end_at = serializers.DateTimeField(help_text="Slot end (ISO 8601).")
    duration_minutes = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(choices=_SLOT_STATUS, help_text="Slot availability status.")
    access_policy = serializers.ChoiceField(
        choices=_ACCESS_POLICY, required=False, help_text="Play-together visibility."
    )
    max_players = serializers.IntegerField(allow_null=True, required=False)
    blocked_reason = serializers.CharField(allow_null=True, required=False)
    booking_id = serializers.UUIDField(allow_null=True, required=False)
    is_last_minute = serializers.BooleanField(required=False)
    notes = serializers.CharField(allow_null=True, required=False)


class SlotListSerializer(serializers.Serializer):
    results = SlotSerializer(many=True)


class CreateSlotSerializer(serializers.Serializer):
    court_id = serializers.UUIDField(help_text="Court the slot belongs to.")
    start_at = serializers.DateTimeField(help_text="Slot start (ISO 8601). Must be within operating hours.")
    end_at = serializers.DateTimeField(help_text="Slot end (ISO 8601). Must be within operating hours.")
    status = serializers.ChoiceField(choices=_SLOT_STATUS, required=False, help_text="Defaults to `open`.")
    is_owner_slot = serializers.BooleanField(
        required=False, help_text="If true, status is forced to `blocked` (no payment flow)."
    )


class BlockSlotSerializer(serializers.Serializer):
    blocked_reason = serializers.CharField(required=False, help_text="Optional human-readable reason.")


class RecurrenceSerializer(serializers.Serializer):
    days_of_week = serializers.ListField(
        child=serializers.ChoiceField(choices=["mon", "tue", "wed", "thu", "fri", "sat", "sun"]),
        help_text="Weekdays to create slots on.",
    )
    start_time = serializers.CharField(help_text="Slot start time `HH:MM` (UTC).")
    end_time = serializers.CharField(help_text="Slot end time `HH:MM` (UTC).")
    from_date = serializers.DateField(help_text="First day of recurrence `YYYY-MM-DD`.")
    until_date = serializers.DateField(help_text="Last day of recurrence `YYYY-MM-DD`.")


class RecurrenceResultSerializer(serializers.Serializer):
    created = serializers.IntegerField(help_text="Number of slots created.")
    results = SlotSerializer(many=True, required=False)


class SlotAccessSerializer(serializers.Serializer):
    access_policy = serializers.ChoiceField(choices=_ACCESS_POLICY, help_text="`open` or `private` (required).")
    max_players = serializers.IntegerField(allow_null=True, required=False)


class JoinRequestSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slot_id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["pending", "approved", "rejected"])
    requested_at = serializers.DateTimeField()


class ParticipantSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slot_id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    joined_at = serializers.DateTimeField()
    payment_status = serializers.CharField(allow_null=True, required=False)
    payment_method = serializers.CharField(allow_null=True, required=False)


class SlotParticipantsResultSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()
    participants = ParticipantSerializer(many=True)
    join_requests = JoinRequestSerializer(many=True)


class JoinStatusResultSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField()
    user_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=["pending", "approved", "rejected", "none"],
        help_text="`none` when the user has no request for this slot.",
    )


class SportsCenterScheduleSerializer(serializers.Serializer):
    date = serializers.DateField()
    courts = serializers.ListField(
        child=serializers.JSONField(), help_text="Courts, each with an embedded `slots` array."
    )


class BookingSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    slot_id = serializers.UUIDField(allow_null=True, required=False)
    court_id = serializers.UUIDField(allow_null=True, required=False)
    user_id = serializers.UUIDField(allow_null=True, required=False)
    status = serializers.ChoiceField(choices=_BOOKING_STATUS)
    customer_name = serializers.CharField(allow_null=True, required=False)
    customer_phone = serializers.CharField(allow_null=True, required=False)
    notes = serializers.CharField(allow_null=True, required=False)
    booking_series_id = serializers.UUIDField(allow_null=True, required=False)
    created_at = serializers.DateTimeField(required=False)


class BookingListSerializer(serializers.Serializer):
    count = serializers.IntegerField(required=False)
    page = serializers.IntegerField(required=False)
    page_size = serializers.IntegerField(required=False)
    results = BookingSerializer(many=True)


class CreateBookingSerializer(serializers.Serializer):
    slot_id = serializers.UUIDField(help_text="Slot to book (required). Must be `open`.")
    booking_series_id = serializers.UUIDField(
        required=False, allow_null=True, help_text="If present, booking is always created `pending`."
    )
    customer_name = serializers.CharField(required=False)
    customer_phone = serializers.CharField(required=False)
    notes = serializers.CharField(required=False)


class ManualBookingSerializer(serializers.Serializer):
    court_id = serializers.UUIDField(help_text="Court to book (owner must own it).")
    date = serializers.DateField(help_text="Booking date `YYYY-MM-DD`.")
    start_time = serializers.CharField(help_text="Start time `HH:MM`.")
    end_time = serializers.CharField(help_text="End time `HH:MM` (must be after start).")
    customer_name = serializers.CharField(required=False, help_text="Walk-in customer name.")
    customer_phone = serializers.CharField(required=False, help_text="E.164 phone number.")
    price_per_hour_override = serializers.FloatField(
        required=False, min_value=0, help_text="Override the court's default hourly price."
    )
    notes = serializers.CharField(required=False)


class BookingStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=["confirmed", "cancelled", "completed"],
        help_text="Target status. Allowed transitions depend on caller role and current status.",
    )


class PriceEstimateSerializer(serializers.Serializer):
    court_id = serializers.UUIDField()
    start_at = serializers.DateTimeField()
    end_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField()
    price_per_hour = serializers.FloatField()
    total_price = serializers.FloatField(help_text="Computed price for the window.")


# Reusable query parameters --------------------------------------------------
def _q(name, typ, required, desc):
    return OpenApiParameter(name=name, type=typ, location=OpenApiParameter.QUERY, required=required, description=desc)


_CONFLICT = OpenApiResponse(ErrorSerializer, description="Conflict — resource state prevents the operation.")


# ===========================================================================
# Courts
# ===========================================================================
@extend_schema(
    tags=["Courts"],
    methods=["GET"],
    auth=[],
    operation_id="courts_list",
    summary="List courts",
    description="Public, paginated, filterable list of courts.",
    parameters=[
        _q("page", OpenApiTypes.INT, False, "Page number (default 1)."),
        _q("page_size", OpenApiTypes.INT, False, "Items per page (default 20)."),
        _q("sport", OpenApiTypes.STR, False, "Filter by sport type."),
        _q("status", OpenApiTypes.STR, False, "Filter by court status."),
    ],
    responses={200: OpenApiResponse(CourtListSerializer, description="Matching courts."), 503: _UPSTREAM},
)
@extend_schema(
    tags=["Courts"],
    methods=["POST"],
    summary="Create court",
    description="Create a court. Owner role required.",
    request=CreateCourtSerializer,
    responses={
        201: OpenApiResponse(CourtSerializer, description="Court created."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        503: _UPSTREAM,
    },
)
@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def courts_list(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Courts"],
    methods=["GET"],
    auth=[],
    operation_id="court_retrieve",
    summary="Get court detail",
    description="Public court detail by id.",
    responses={200: OpenApiResponse(CourtSerializer, description="Court detail."), 404: _NOT_FOUND, 503: _UPSTREAM},
)
@extend_schema(
    tags=["Courts"],
    methods=["PATCH"],
    summary="Update court",
    description="Update a court. Owner only.",
    request=UpdateCourtSerializer,
    responses={
        200: OpenApiResponse(CourtSerializer, description="Updated court."),
        207: OpenApiResponse(description="Partial success (multi-status)."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        409: _CONFLICT,
        503: _UPSTREAM,
    },
)
@extend_schema(
    tags=["Courts"],
    methods=["DELETE"],
    summary="Delete court",
    description="Soft-delete a court. Owner only.",
    responses={200: OpenApiResponse(description="Court soft-deleted."), 401: _UNAUTHORIZED, 403: _FORBIDDEN_ROLE, 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["GET", "PATCH", "DELETE"])
@authentication_classes([])
@permission_classes([AllowAny])
def court_detail(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Courts"],
    auth=[],
    summary="Look up court by slug",
    description="Resolve a court slug to full detail. Case-insensitive; only `approved` courts match.",
    responses={200: OpenApiResponse(CourtSerializer, description="Court detail."), 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def court_by_slug(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Courts"],
    auth=[],
    summary="Nearby courts",
    description="Courts sorted by distance from a location (Haversine).",
    parameters=[
        _q("lat", OpenApiTypes.NUMBER, True, "Caller latitude."),
        _q("lng", OpenApiTypes.NUMBER, True, "Caller longitude."),
        _q("radius_km", OpenApiTypes.INT, False, "Search radius: 1 | 3 | 5 (default 5)."),
        _q("sport", OpenApiTypes.STR, False, "Filter by sport type."),
        _q("price_min", OpenApiTypes.NUMBER, False, "Minimum price_per_hour."),
        _q("price_max", OpenApiTypes.NUMBER, False, "Maximum price_per_hour."),
        _q("time_of_day", OpenApiTypes.STR, False, "morning | afternoon | evening | night."),
    ],
    responses={200: OpenApiResponse(CourtListSerializer, description="Nearby courts."), 400: _BAD_JSON, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def courts_nearby(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Courts"],
    auth=[],
    summary="Court slots in date range",
    description="All slots for a court within an inclusive date range. Public.",
    parameters=[
        _q("from", OpenApiTypes.DATE, True, "Inclusive start date `YYYY-MM-DD`."),
        _q("to", OpenApiTypes.DATE, True, "End date boundary `YYYY-MM-DD`."),
    ],
    responses={200: OpenApiResponse(SlotListSerializer, description="Slots in range."), 400: _BAD_JSON, 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def court_slots_range(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Courts"],
    summary="Update court settings",
    description="Toggle the `auto_approve_single` flag. Owner only.",
    request=CourtSettingsSerializer,
    responses={
        200: OpenApiResponse(CourtSettingsResultSerializer, description="Updated settings."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@api_view(["PATCH"])
@authentication_classes([])
def court_settings(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    auth=[],
    summary="Sports center day schedule",
    description="All courts of a sports center plus their slots for a given day. Public.",
    parameters=[_q("date", OpenApiTypes.DATE, True, "Day `YYYY-MM-DD`.")],
    responses={200: OpenApiResponse(SportsCenterScheduleSerializer, description="Schedule."), 400: _BAD_JSON, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def sports_center_schedule(request, *args, **kwargs):
    return _stub(request)


# ===========================================================================
# Slots
# ===========================================================================
@extend_schema(
    tags=["Slots"],
    summary="Create slot",
    description="Create an availability slot. Owner only. Must be within operating hours and not overlap.",
    request=CreateSlotSerializer,
    responses={
        201: OpenApiResponse(SlotSerializer, description="Slot created."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="Overlapping slot conflict."),
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
def slots_create(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Block slot",
    description="Set slot status to `blocked`. Owner only. 409 if the slot is currently booked.",
    request=BlockSlotSerializer,
    responses={
        200: OpenApiResponse(SlotSerializer, description="Slot blocked."),
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="Cannot block a booked slot."),
        503: _UPSTREAM,
    },
)
@api_view(["PATCH"])
@authentication_classes([])
def slot_block(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Unblock slot",
    description="Set slot status back to `open` and clear the block reason. Owner only.",
    request=None,
    responses={200: OpenApiResponse(SlotSerializer, description="Slot unblocked."), 401: _UNAUTHORIZED, 403: _FORBIDDEN_ROLE, 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["PATCH"])
@authentication_classes([])
def slot_unblock(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Create recurring slots",
    description="Generate open-availability slots on a recurring weekly schedule. Owner only.",
    request=RecurrenceSerializer,
    responses={
        200: OpenApiResponse(RecurrenceResultSerializer, description="Slots generated."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
def recurrence_create(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    auth=[],
    summary="Get slot detail",
    description="Full slot detail incl. court name and computed duration. Public.",
    responses={200: OpenApiResponse(SlotSerializer, description="Slot detail."), 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def slot_detail(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    auth=[],
    summary="Open slots for join",
    description="Open, future, joinable slots near the player, sorted by `start_at`. Public.",
    parameters=[
        _q("lat", OpenApiTypes.NUMBER, True, "Caller latitude."),
        _q("lng", OpenApiTypes.NUMBER, True, "Caller longitude."),
        _q("radius_km", OpenApiTypes.INT, False, "Radius: 1 | 3 | 5 (default 5)."),
        _q("sport", OpenApiTypes.STR, False, "Filter courts by sport type."),
    ],
    responses={200: OpenApiResponse(SlotListSerializer, description="Open joinable slots."), 400: _BAD_JSON, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def open_slots_for_join(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Set slot access policy",
    description="Booking owner sets `access_policy` (open|private) and optionally `max_players`.",
    request=SlotAccessSerializer,
    responses={
        200: OpenApiResponse(SlotSerializer, description="Updated slot."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: OpenApiResponse(ErrorSerializer, description="Caller is not the booking owner."),
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@api_view(["PATCH"])
@authentication_classes([])
def slot_access(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Request to join slot",
    description="Player requests to join an open slot. Creates a pending join request.",
    request=None,
    responses={
        201: OpenApiResponse(JoinRequestSerializer, description="Join request created."),
        401: _UNAUTHORIZED,
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="Slot private, or duplicate request."),
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
def slot_join(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Slot participants & requests",
    description="Confirmed participants plus pending join requests for a slot. Any authenticated user.",
    responses={200: OpenApiResponse(SlotParticipantsResultSerializer, description="Participants & requests."), 401: _UNAUTHORIZED, 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
def slot_participants(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="My join status for slot",
    description="Current join status for the slot (defaults to the authenticated user).",
    parameters=[_q("user_id", OpenApiTypes.UUID, False, "Player UUID to check (default: caller).")],
    responses={200: OpenApiResponse(JoinStatusResultSerializer, description="Join status."), 401: _UNAUTHORIZED, 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
def slot_join_status(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Mark slot last-minute",
    description="Owner marks a slot last-minute available and triggers FCM push to nearby users.",
    request=None,
    responses={
        200: OpenApiResponse(SlotSerializer, description="Slot updated (`is_last_minute=true`)."),
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
def slot_last_minute(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Approve join request",
    description="Slot booking owner approves a pending join request; adds a participant + notifies requester.",
    request=None,
    responses={
        200: OpenApiResponse(JoinRequestSerializer, description="Request approved."),
        401: _UNAUTHORIZED,
        403: OpenApiResponse(ErrorSerializer, description="Not the slot booking owner."),
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="Request already processed."),
        503: _UPSTREAM,
    },
)
@api_view(["PATCH"])
@authentication_classes([])
def slot_join_approve(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Slots"],
    summary="Reject join request",
    description="Slot booking owner rejects a pending join request; notifies requester.",
    request=None,
    responses={
        200: OpenApiResponse(JoinRequestSerializer, description="Request rejected."),
        401: _UNAUTHORIZED,
        403: OpenApiResponse(ErrorSerializer, description="Not the slot booking owner."),
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="Request already processed."),
        503: _UPSTREAM,
    },
)
@api_view(["PATCH"])
@authentication_classes([])
def slot_join_reject(request, *args, **kwargs):
    return _stub(request)


# ===========================================================================
# Bookings
# ===========================================================================
@extend_schema(
    tags=["Bookings"],
    summary="Create booking",
    description="Atomically create a single-time booking for an open slot.",
    request=CreateBookingSerializer,
    responses={
        201: OpenApiResponse(BookingSerializer, description="Booking created."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="Slot unavailable (status != open)."),
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
def booking_create(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Bookings"],
    summary="Create manual / walk-in booking",
    description="Owner creates a booking for an in-person customer. Owner must own the court; auto-creates the slot.",
    request=ManualBookingSerializer,
    responses={
        201: OpenApiResponse(BookingSerializer, description="Booking created."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="A non-open slot already exists for that window."),
        503: _UPSTREAM,
    },
)
@api_view(["POST"])
@authentication_classes([])
def manual_booking(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Bookings"],
    summary="Price estimate",
    description="Price estimate for a court + time window. Any authenticated user.",
    parameters=[
        _q("court_id", OpenApiTypes.UUID, True, "Court UUID."),
        _q("start_at", OpenApiTypes.DATETIME, True, "Window start (ISO 8601)."),
        _q("end_at", OpenApiTypes.DATETIME, True, "Window end (ISO 8601)."),
    ],
    responses={200: OpenApiResponse(PriceEstimateSerializer, description="Price breakdown."), 400: _BAD_JSON, 401: _UNAUTHORIZED, 404: _NOT_FOUND, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
def price_estimate(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Bookings"],
    summary="List bookings",
    description="Paginated bookings visible to the caller (player: own; owner: their courts').",
    parameters=[
        _q("court_id", OpenApiTypes.UUID, False, "Filter by court UUID."),
        _q("status", OpenApiTypes.STR, False, "pending | confirmed | cancelled | completed."),
        _q("from_date", OpenApiTypes.DATE, False, "Created on/after `YYYY-MM-DD`."),
        _q("to_date", OpenApiTypes.DATE, False, "Created on/before `YYYY-MM-DD`."),
        _q("page", OpenApiTypes.INT, False, "Page number (default 1)."),
        _q("page_size", OpenApiTypes.INT, False, "Items per page."),
    ],
    responses={200: OpenApiResponse(BookingListSerializer, description="Bookings page."), 401: _UNAUTHORIZED, 503: _UPSTREAM},
)
@api_view(["GET"])
@authentication_classes([])
def booking_list(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Bookings"],
    summary="Transition booking status",
    description=(
        "Transition a booking's status. Allowed: pending→confirmed (owner), "
        "pending/confirmed→cancelled (owner or booking player), confirmed→completed (owner). "
        "Cancellation restores the linked slot to `open`."
    ),
    request=BookingStatusSerializer,
    responses={
        200: OpenApiResponse(BookingSerializer, description="Updated booking."),
        400: _BAD_JSON,
        401: _UNAUTHORIZED,
        403: _FORBIDDEN_ROLE,
        404: _NOT_FOUND,
        409: OpenApiResponse(ErrorSerializer, description="Illegal status transition."),
        503: _UPSTREAM,
    },
)
@api_view(["PATCH"])
@authentication_classes([])
def booking_status(request, *args, **kwargs):
    return _stub(request)


@extend_schema(
    tags=["Bookings"],
    summary="Get booking detail",
    description="Single booking by id (player: own; owner: any for their courts).",
    responses={
        200: OpenApiResponse(BookingSerializer, description="Booking detail."),
        401: _UNAUTHORIZED,
        403: OpenApiResponse(ErrorSerializer, description="Not the booking player nor the court owner."),
        404: _NOT_FOUND,
        503: _UPSTREAM,
    },
)
@api_view(["GET"])
@authentication_classes([])
def booking_detail(request, *args, **kwargs):
    return _stub(request)


# ---------------------------------------------------------------------------
# Documentation-only URLConf — consumed exclusively by
# SpectacularAPIView(urlconf="spb_core.api_docs"). Paths mirror the real routes.
# ---------------------------------------------------------------------------
urlpatterns = [
    path("health/", health),
    # auth_ext (prefix /auth/)
    path("auth/owner/signup", owner_signup),
    path("auth/owner/login", owner_login),
    path("auth/owner/forgot-password", owner_forgot_password),
    path("auth/refresh", token_refresh),
    path("auth/player/signup", player_signup),
    path("auth/callback", auth_callback),
    path("auth/player/login", player_login),
    path("auth/player/forgot-password", player_forgot_password),
    path("auth/player/resend-verification", player_resend_verification),
    path("auth/player/google", player_google_oauth),
    # players (prefix /api/players/)
    path("api/players/me", players_me),
    path("api/players/me/fcm-token", players_fcm_token),
    path("api/players/me/location", players_me_location),
    path("api/players/me/avatar", players_me_avatar),
    # courts (prefix /api/courts/) — specific routes before <court_id> catch-all
    path("api/courts/", courts_list),
    path("api/courts/by-slug/<str:slug>", court_by_slug),
    path("api/courts/nearby", courts_nearby),
    path("api/courts/<str:court_id>/recurrence", recurrence_create),
    path("api/courts/<str:court_id>/slots", court_slots_range),
    path("api/courts/<str:court_id>/settings", court_settings),
    path("api/courts/<str:court_id>/", court_detail),
    path("api/sports-centers/<str:sc_id>/schedule", sports_center_schedule),
    # slots
    path("api/courts/slots", slots_create),
    path("api/courts/slots/<str:slot_id>/block", slot_block),
    path("api/courts/slots/<str:slot_id>/unblock", slot_unblock),
    path("api/slots/open-for-join", open_slots_for_join),
    path("api/slots/<str:slot_id>", slot_detail),
    path("api/slots/<str:slot_id>/access", slot_access),
    path("api/slots/<str:slot_id>/join", slot_join),
    path("api/slots/<str:slot_id>/participants", slot_participants),
    path("api/slots/<str:slot_id>/join-status", slot_join_status),
    path("api/slots/<str:slot_id>/last-minute", slot_last_minute),
    path("api/slot-join-requests/<str:join_request_id>/approve", slot_join_approve),
    path("api/slot-join-requests/<str:join_request_id>/reject", slot_join_reject),
    # bookings (prefix /api/bookings)
    path("api/bookings", booking_create),
    path("api/bookings/manual", manual_booking),
    path("api/bookings/price-estimate", price_estimate),
    path("api/bookings/list", booking_list),
    path("api/bookings/<str:booking_id>/status", booking_status),
    path("api/bookings/<str:booking_id>", booking_detail),
]
