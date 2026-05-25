"""
players.validators — Reusable validators for the players app.

validate_avatar_file(file):
    Validates an uploaded avatar file for size and MIME type.
    Raises django.core.exceptions.ValidationError on failure.
    Intended for use in multipart-form upload views.
"""

from django.core.exceptions import ValidationError

_MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB in bytes
_ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png"})


def validate_avatar_file(file) -> None:
    """
    Validate that *file* is an acceptable avatar upload.

    Checks (in order):
    1. Size must not exceed 2 MB.
    2. MIME type must be ``image/jpeg`` or ``image/png``.

    Parameters
    ----------
    file:
        Any object exposing ``.size`` (int, bytes) and ``.content_type`` (str).
        Compatible with Django's ``InMemoryUploadedFile`` and
        ``TemporaryUploadedFile``.

    Raises
    ------
    django.core.exceptions.ValidationError
        If size or MIME type is invalid.
    """
    if file.size > _MAX_AVATAR_SIZE:
        raise ValidationError("File too large. Maximum size is 2 MB.")

    if file.content_type not in _ALLOWED_MIME_TYPES:
        raise ValidationError("Invalid file type. Only JPEG and PNG are allowed.")
