"""
Tests for players.validators.validate_avatar_file.

Covers:
- File too large (> 2 MB) → raises ValidationError with expected message
- Wrong MIME type → raises ValidationError with expected message
- Valid JPEG → passes without error
- Valid PNG → passes without error
- Exactly 2 MB boundary → passes (inclusive boundary)
- Empty/zero-size file with valid MIME → passes size check
"""
import io
import pytest
from django.core.exceptions import ValidationError


class _FakeFile:
    """Minimal in-memory file-like object that mimics Django's UploadedFile."""

    def __init__(self, size: int, content_type: str):
        self.size = size
        self.content_type = content_type
        self.name = "test_avatar.jpg"


MAX_SIZE = 2 * 1024 * 1024  # 2 MB in bytes


class TestValidateAvatarFile:
    def _make_file(self, size=MAX_SIZE, content_type="image/jpeg"):
        return _FakeFile(size=size, content_type=content_type)

    # ── size validation ──────────────────────────────────────────────────────

    def test_file_too_large_raises_validation_error(self):
        """File exceeding 2 MB must raise ValidationError."""
        from players.validators import validate_avatar_file

        file = self._make_file(size=MAX_SIZE + 1)
        with pytest.raises(ValidationError) as exc_info:
            validate_avatar_file(file)
        assert "File too large" in str(exc_info.value)
        assert "2 MB" in str(exc_info.value)

    def test_file_exactly_2mb_is_accepted(self):
        """File of exactly 2 MB must pass without raising."""
        from players.validators import validate_avatar_file

        file = self._make_file(size=MAX_SIZE)
        # Should not raise
        validate_avatar_file(file)

    def test_file_under_2mb_is_accepted(self):
        """File under 2 MB must pass."""
        from players.validators import validate_avatar_file

        file = self._make_file(size=MAX_SIZE - 1)
        validate_avatar_file(file)

    def test_zero_size_file_is_accepted_for_size_check(self):
        """Zero-byte file should pass size check (content check is separate)."""
        from players.validators import validate_avatar_file

        file = self._make_file(size=0, content_type="image/jpeg")
        validate_avatar_file(file)

    # ── MIME type validation ──────────────────────────────────────────────────

    def test_jpeg_mime_is_accepted(self):
        """image/jpeg must pass MIME check."""
        from players.validators import validate_avatar_file

        validate_avatar_file(self._make_file(content_type="image/jpeg"))

    def test_png_mime_is_accepted(self):
        """image/png must pass MIME check."""
        from players.validators import validate_avatar_file

        validate_avatar_file(self._make_file(content_type="image/png"))

    def test_gif_mime_raises_validation_error(self):
        """image/gif must raise ValidationError."""
        from players.validators import validate_avatar_file

        file = self._make_file(content_type="image/gif")
        with pytest.raises(ValidationError) as exc_info:
            validate_avatar_file(file)
        assert "Invalid file type" in str(exc_info.value)
        assert "JPEG" in str(exc_info.value)
        assert "PNG" in str(exc_info.value)

    def test_webp_mime_raises_validation_error(self):
        """image/webp must raise ValidationError."""
        from players.validators import validate_avatar_file

        file = self._make_file(content_type="image/webp")
        with pytest.raises(ValidationError):
            validate_avatar_file(file)

    def test_pdf_mime_raises_validation_error(self):
        """application/pdf must raise ValidationError."""
        from players.validators import validate_avatar_file

        file = self._make_file(content_type="application/pdf")
        with pytest.raises(ValidationError):
            validate_avatar_file(file)

    def test_text_plain_mime_raises_validation_error(self):
        """text/plain must raise ValidationError."""
        from players.validators import validate_avatar_file

        file = self._make_file(content_type="text/plain")
        with pytest.raises(ValidationError):
            validate_avatar_file(file)

    def test_empty_mime_raises_validation_error(self):
        """Empty/blank content_type must raise ValidationError."""
        from players.validators import validate_avatar_file

        file = self._make_file(content_type="")
        with pytest.raises(ValidationError):
            validate_avatar_file(file)

    # ── combined (size + MIME) ────────────────────────────────────────────────

    def test_large_file_with_wrong_mime_raises_error(self):
        """Both violations at once; size check fires first."""
        from players.validators import validate_avatar_file

        file = self._make_file(size=MAX_SIZE + 1, content_type="image/gif")
        with pytest.raises(ValidationError):
            validate_avatar_file(file)

    # ── error message format ──────────────────────────────────────────────────

    def test_size_error_message_exact(self):
        """Error message for oversized file must match API contract."""
        from players.validators import validate_avatar_file

        file = self._make_file(size=MAX_SIZE + 1)
        with pytest.raises(ValidationError) as exc_info:
            validate_avatar_file(file)
        # The message list in ValidationError
        messages = exc_info.value.messages
        assert any("File too large. Maximum size is 2 MB." in m for m in messages)

    def test_mime_error_message_exact(self):
        """Error message for wrong MIME must match API contract."""
        from players.validators import validate_avatar_file

        file = self._make_file(content_type="image/gif")
        with pytest.raises(ValidationError) as exc_info:
            validate_avatar_file(file)
        messages = exc_info.value.messages
        assert any("Invalid file type. Only JPEG and PNG are allowed." in m for m in messages)
