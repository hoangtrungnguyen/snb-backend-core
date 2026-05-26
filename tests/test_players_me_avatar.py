"""
Tests for POST /api/players/me/avatar endpoint.

Covers:
- Unauthenticated → 401
- Non-player role → 403
- No file in request → 400
- File too large (> 2 MB) → 400
- Wrong MIME type → 400
- Supabase Storage upload failure → 503
- Supabase profile PATCH failure → 503
- Successful upload → 200 with avatar_url containing the storage path
- GET/PATCH on avatar route → 405
"""
import requests as req_lib
from io import BytesIO
from unittest.mock import patch, MagicMock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client

MAX_SIZE = 2 * 1024 * 1024  # 2 MB


def _make_upload_file(size=1024, content_type="image/jpeg", name="avatar.jpg"):
    return SimpleUploadedFile(name, b"x" * size, content_type=content_type)


def _mock_storage_ok():
    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {"Key": "avatars/test-id/avatar.jpg"}
    return m


def _mock_patch_ok():
    m = MagicMock()
    m.status_code = 204
    return m


_PLAYER_PAYLOAD = {
    "sub": "550e8400-e29b-41d4-a716-446655440000",
    "email": "player@example.com",
    "app_metadata": {"role": "player"},
}

_OWNER_PAYLOAD = {
    "sub": "550e8400-e29b-41d4-a716-446655440000",
    "email": "owner@example.com",
    "app_metadata": {"role": "owner"},
}


class PlayersMeAvatarViewTests(TestCase):

    def setUp(self):
        self.client = Client()
        self.url = "/api/players/me/avatar"
        self.token = "eyJ.valid.token"

    def _post(self, file=None, token=None):
        headers = {}
        if token is not False:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token or self.token}"
        data = {}
        if file is not None:
            data["avatar"] = file
        return self.client.post(self.url, data=data, **headers)

    # ------------------------------------------------------------------
    # Authentication / authorisation
    # ------------------------------------------------------------------

    def test_no_auth_returns_401(self):
        resp = self._post(token=False)
        self.assertEqual(resp.status_code, 401)

    def test_invalid_token_returns_401(self):
        with patch("auth_ext.middleware._decode_token", return_value=None):
            resp = self._post(_make_upload_file(), token="bad.token")
        self.assertIn(resp.status_code, [401, 403])

    def test_non_player_role_returns_403(self):
        with patch("auth_ext.middleware._decode_token", return_value=_OWNER_PAYLOAD):
            resp = self._post(_make_upload_file())
        self.assertEqual(resp.status_code, 403)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def test_missing_file_returns_400(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post(file=None)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())

    def test_file_too_large_returns_400(self):
        f = _make_upload_file(size=MAX_SIZE + 1)
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)
        self.assertIn("2 MB", body["error"])

    def test_wrong_mime_type_returns_400(self):
        f = _make_upload_file(content_type="image/gif", name="avatar.gif")
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIn("error", body)
        self.assertIn("JPEG", body["error"])

    def test_pdf_mime_returns_400(self):
        f = _make_upload_file(content_type="application/pdf", name="file.pdf")
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------------
    # Storage / profile update failures
    # ------------------------------------------------------------------

    def test_storage_upload_network_error_returns_503(self):
        f = _make_upload_file()
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post",
                   side_effect=req_lib.RequestException("connection refused")):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertIn("error", body)
        self.assertNotIn("connection refused", resp.content.decode())

    def test_storage_upload_non_200_returns_503(self):
        f = _make_upload_file()
        mock_fail = MagicMock()
        mock_fail.status_code = 400
        mock_fail.json.return_value = {"error": "Bucket not found"}
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=mock_fail):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 503)

    def test_profile_patch_failure_returns_503(self):
        f = _make_upload_file()
        mock_patch_fail = MagicMock()
        mock_patch_fail.status_code = 500
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_storage_ok()), \
             patch("players.views.requests.patch", return_value=mock_patch_fail):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 503)

    def test_profile_patch_network_error_returns_503(self):
        f = _make_upload_file()
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_storage_ok()), \
             patch("players.views.requests.patch",
                   side_effect=req_lib.RequestException("timeout")):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 503)

    # ------------------------------------------------------------------
    # Success path
    # ------------------------------------------------------------------

    def test_successful_jpeg_upload_returns_200_with_avatar_url(self):
        f = _make_upload_file(content_type="image/jpeg")
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_storage_ok()), \
             patch("players.views.requests.patch", return_value=_mock_patch_ok()):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("avatar_url", body)
        self.assertIn("avatars", body["avatar_url"])
        self.assertIn("550e8400", body["avatar_url"])
        self.assertTrue(body["avatar_url"].endswith(".jpg"))

    def test_successful_png_upload_returns_200_with_png_extension(self):
        f = _make_upload_file(content_type="image/png", name="avatar.png")
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_storage_ok()), \
             patch("players.views.requests.patch", return_value=_mock_patch_ok()):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("avatar_url", body)
        self.assertTrue(body["avatar_url"].endswith(".png"))

    def test_avatar_url_uses_supabase_public_storage_path(self):
        f = _make_upload_file()
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_storage_ok()), \
             patch("players.views.requests.patch", return_value=_mock_patch_ok()):
            resp = self._post(f)
        url = resp.json()["avatar_url"]
        self.assertIn("/storage/v1/object/public/", url)

    def test_storage_upload_called_with_correct_content_type(self):
        f = _make_upload_file(content_type="image/png", name="avatar.png")
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD) as _, \
             patch("players.views.requests.post", return_value=_mock_storage_ok()) as mock_post, \
             patch("players.views.requests.patch", return_value=_mock_patch_ok()):
            self._post(f)
        call_kwargs = mock_post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        self.assertEqual(headers.get("Content-Type"), "image/png")

    def test_profile_patch_sends_avatar_url(self):
        f = _make_upload_file()
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_storage_ok()), \
             patch("players.views.requests.patch", return_value=_mock_patch_ok()) as mock_patch:
            self._post(f)
        call_json = mock_patch.call_args.kwargs.get("json") or mock_patch.call_args[1].get("json", {})
        self.assertIn("avatar_url", call_json)

    def test_exactly_2mb_file_succeeds(self):
        f = _make_upload_file(size=MAX_SIZE)
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD), \
             patch("players.views.requests.post", return_value=_mock_storage_ok()), \
             patch("players.views.requests.patch", return_value=_mock_patch_ok()):
            resp = self._post(f)
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------------
    # HTTP method guard
    # ------------------------------------------------------------------

    def test_get_method_returns_405(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.get(
                self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}"
            )
        self.assertEqual(resp.status_code, 405)

    def test_patch_method_returns_405(self):
        with patch("auth_ext.middleware._decode_token", return_value=_PLAYER_PAYLOAD):
            resp = self.client.patch(
                self.url, HTTP_AUTHORIZATION=f"Bearer {self.token}"
            )
        self.assertEqual(resp.status_code, 405)
