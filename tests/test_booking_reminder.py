"""
Tests for push booking reminder (grava-52bc.3).

Covers:
- grava-52bc.3.1: pg_cron migration 0014 creates mark_reminder_candidates() SQL function
- grava-52bc.3.2: Management command polls reminder candidates via Supabase REST API
- grava-52bc.3.3: FCM push sent to all users.fcm_tokens for each candidate
- grava-52bc.3.4: Notification payload title, body, deep-link data
- grava-52bc.3.5: reminder_sent = true set after successful send
- grava-52bc.3.6: Bookings with empty fcm_tokens silently skipped, logged
- grava-52bc.3.7: Failed FCM send retried once; permanent failure logged, continue
- grava-52bc.3.8: Series occurrences treated identically (each row independent)
"""

import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOOKING_ID = str(uuid.uuid4())
_BOOKING_ID_2 = str(uuid.uuid4())
_SERIES_BOOKING_ID = str(uuid.uuid4())
_USER_ID = str(uuid.uuid4())
_COURT_ID = str(uuid.uuid4())
_SLOT_ID = str(uuid.uuid4())
_COURT_NAME = "Sân ABC"
_COURT_ADDRESS = "123 Đường Lê Lợi, Quận 1"


def _reminder_candidate(
    booking_id=_BOOKING_ID,
    user_id=_USER_ID,
    court_name=_COURT_NAME,
    court_address=_COURT_ADDRESS,
    start_at=None,
    booking_series_id=None,
):
    """Return a fake reminder candidate row (joined booking + slot + court)."""
    if start_at is None:
        start_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    return {
        "id": booking_id,
        "user_id": user_id,
        "status": "confirmed",
        "reminder_sent": False,
        "court_id": _COURT_ID,
        "slot_id": _SLOT_ID,
        "booking_series_id": booking_series_id,
        "court_name": court_name,
        "court_address": court_address,
        "start_at": start_at,
    }


def _ok_resp(data, status=200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    return m


def _err_resp(status=500):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = {"message": "error"}
    return m


# ---------------------------------------------------------------------------
# grava-52bc.3.1: Alembic migration 0014 (pg_cron) (grava-52bc.3.1)
# ---------------------------------------------------------------------------

class TestMigration0014ReminderCron:
    """Verify the alembic migration for pg_cron reminder scheduling."""

    def test_migration_file_exists(self):
        """0014_reminder_cron.py must exist in alembic/versions/."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0014_reminder_cron.py")
        assert os.path.isfile(path), "0014_reminder_cron.py missing from alembic/versions/"

    def test_migration_has_revision_0014(self):
        """Migration must declare revision = '0014'."""
        import importlib.util
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0014_reminder_cron.py")
        spec = importlib.util.spec_from_file_location("migration_0014", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.revision == "0014"

    def test_migration_upgrade_creates_function_and_cron(self):
        """upgrade() must call op.execute at least twice (function + cron schedule)."""
        import importlib.util
        import os
        import alembic.op as alembic_op
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0014_reminder_cron.py")
        spec = importlib.util.spec_from_file_location("migration_0014", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch.object(alembic_op, "execute") as mock_execute:
            mod.upgrade()
            assert mock_execute.call_count >= 2, (
                "upgrade() must call op.execute at least twice "
                "(mark_reminder_candidates function + pg_cron schedule)"
            )

    def test_migration_upgrade_references_mark_reminder_candidates(self):
        """The upgrade SQL must reference mark_reminder_candidates."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0014_reminder_cron.py")
        with open(path) as f:
            src = f.read()
        assert "mark_reminder_candidates" in src, (
            "0014_reminder_cron.py must define mark_reminder_candidates()"
        )

    def test_migration_upgrade_references_pg_cron(self):
        """The upgrade SQL must reference pg_cron."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0014_reminder_cron.py")
        with open(path) as f:
            src = f.read()
        assert "pg_cron" in src or "cron.schedule" in src, (
            "0014_reminder_cron.py must reference pg_cron scheduling"
        )

    def test_migration_downgrade_removes_function_and_cron(self):
        """downgrade() must call op.execute to clean up."""
        import importlib.util
        import os
        import alembic.op as alembic_op
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "alembic", "versions", "0014_reminder_cron.py")
        spec = importlib.util.spec_from_file_location("migration_0014", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        with patch.object(alembic_op, "execute") as mock_execute:
            mod.downgrade()
            assert mock_execute.call_count >= 1, "downgrade() must clean up cron and function"


# ---------------------------------------------------------------------------
# grava-52bc.3.2: Management command fetches reminder candidates
# ---------------------------------------------------------------------------

class TestSendBookingRemindersFetch:
    """Management command polls for reminder candidates (grava-52bc.3.2)."""

    def _run_command(self, candidates, user_fcm_map=None, patch_mark=None):
        """Helper: run the command with mocked Supabase responses."""
        from notifications.reminder import fetch_reminder_candidates

        if user_fcm_map is None:
            user_fcm_map = {_USER_ID: ["tok1"]}

        supabase_resp = _ok_resp(candidates)

        def fake_get(url, **kwargs):
            if "bookings" in url or "reminder_candidate" in url.lower():
                return supabase_resp
            uid = kwargs.get("params", {}).get("id", "").replace("eq.", "")
            tokens = user_fcm_map.get(uid, [])
            return _ok_resp([{"id": uid, "fcm_tokens": tokens}])

        with patch("notifications.reminder.requests.get", side_effect=fake_get) as mock_get:
            result = fetch_reminder_candidates()
        return result

    def test_fetch_returns_list(self):
        """fetch_reminder_candidates() returns a list of candidate dicts."""
        from notifications.reminder import fetch_reminder_candidates

        candidates = [_reminder_candidate()]
        supabase_resp = _ok_resp(candidates)

        with patch("notifications.reminder.requests.get", return_value=supabase_resp):
            result = fetch_reminder_candidates()

        assert isinstance(result, list)
        assert len(result) == 1

    def test_fetch_filters_confirmed_not_reminded(self):
        """Query must filter status=confirmed and reminder_sent=false."""
        from notifications.reminder import fetch_reminder_candidates

        supabase_resp = _ok_resp([])

        with patch("notifications.reminder.requests.get", return_value=supabase_resp) as mock_get:
            fetch_reminder_candidates()

        call_str = str(mock_get.call_args)
        assert "confirmed" in call_str, "Query must filter by status=confirmed"
        assert "reminder_sent" in call_str or "false" in call_str.lower(), (
            "Query must filter by reminder_sent=false"
        )

    def test_fetch_returns_empty_on_supabase_error(self):
        """On Supabase error, fetch returns [] (does not raise)."""
        from notifications.reminder import fetch_reminder_candidates

        with patch("notifications.reminder.requests.get", return_value=_err_resp(500)):
            result = fetch_reminder_candidates()

        assert result == []

    def test_fetch_returns_empty_on_network_error(self):
        """On network error, fetch returns [] (does not raise)."""
        import requests as req_lib
        from notifications.reminder import fetch_reminder_candidates

        with patch("notifications.reminder.requests.get",
                   side_effect=req_lib.RequestException("timeout")):
            result = fetch_reminder_candidates()

        assert result == []


# ---------------------------------------------------------------------------
# grava-52bc.3.3 + grava-52bc.3.4: FCM push with correct payload
# ---------------------------------------------------------------------------

class TestSendReminderFCM:
    """FCM push sent with correct payload (grava-52bc.3.3 + grava-52bc.3.4)."""

    def test_fcm_sent_to_user_tokens(self):
        """send_booking_reminder sends FCM to all tokens of the booking user."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate()
        tokens = ["tok_a", "tok_b"]
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": tokens}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        mock_fcm.assert_called_once()
        call_tokens = mock_fcm.call_args.kwargs.get("tokens") or mock_fcm.call_args.args[0]
        assert call_tokens == tokens

    def test_fcm_title_is_sap_den_gio_choi(self):
        """FCM title must be 'Sắp đến giờ chơi' (grava-52bc.3.4)."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate()
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        call_kwargs = mock_fcm.call_args.kwargs
        title = call_kwargs.get("title") or mock_fcm.call_args.args[1]
        assert title == "Sắp đến giờ chơi", f"Expected 'Sắp đến giờ chơi', got {title!r}"

    def test_fcm_body_contains_court_name(self):
        """FCM body must contain court_name (grava-52bc.3.4)."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(court_name="Sân XYZ")
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        call_kwargs = mock_fcm.call_args.kwargs
        body = call_kwargs.get("body") or mock_fcm.call_args.args[2]
        assert "Sân XYZ" in body, f"Body must contain court name, got {body!r}"

    def test_fcm_data_has_deep_link(self):
        """FCM data must include deep_link '/bookings/:id' (grava-52bc.3.4)."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        call_kwargs = mock_fcm.call_args.kwargs
        data = call_kwargs.get("data") or mock_fcm.call_args.args[3]
        assert "deep_link" in data, "data must contain 'deep_link'"
        assert _BOOKING_ID in data["deep_link"], f"deep_link must include booking_id"

    def test_fcm_data_has_booking_id(self):
        """FCM data must include booking_id (grava-52bc.3.4)."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        call_kwargs = mock_fcm.call_args.kwargs
        data = call_kwargs.get("data") or mock_fcm.call_args.args[3]
        assert "booking_id" in data, "data must contain 'booking_id'"
        assert data["booking_id"] == _BOOKING_ID


# ---------------------------------------------------------------------------
# grava-52bc.3.5: Sets reminder_sent = true after successful send
# ---------------------------------------------------------------------------

class TestReminderSentFlag:
    """Sets reminder_sent = true after successful FCM send (grava-52bc.3.5)."""

    def test_reminder_sent_patched_to_true(self):
        """After successful FCM, PATCH bookings sets reminder_sent=true."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp) as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast"):
            send_booking_reminder(booking)

        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        body = call_args.kwargs.get("json") or {}
        assert body.get("reminder_sent") is True, "Must PATCH reminder_sent=true"

    def test_reminder_sent_patch_targets_correct_booking(self):
        """PATCH must target the specific booking row by id (in URL or params)."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp) as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast"):
            send_booking_reminder(booking)

        # Booking ID must appear either in the URL or in the query params
        full_call_str = str(mock_patch.call_args)
        assert _BOOKING_ID in full_call_str, (
            f"PATCH call must reference booking id {_BOOKING_ID}. "
            f"Full call: {full_call_str}"
        )

    def test_reminder_sent_not_patched_if_no_tokens(self):
        """If user has no FCM tokens, reminder_sent must NOT be updated."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate()
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": []}])

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch") as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast"):
            send_booking_reminder(booking)

        mock_patch.assert_not_called()


# ---------------------------------------------------------------------------
# grava-52bc.3.6: Empty fcm_tokens → skip silently, log
# ---------------------------------------------------------------------------

class TestEmptyTokensSkip:
    """Players with empty fcm_tokens skipped silently, logged (grava-52bc.3.6)."""

    def test_empty_tokens_skips_fcm(self):
        """When fcm_tokens is [], FCM must not be called."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate()
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": []}])

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch") as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        mock_fcm.assert_not_called()
        mock_patch.assert_not_called()  # reminder_sent must not be set

    def test_null_tokens_skips_fcm(self):
        """When fcm_tokens is null/None, FCM must not be called."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate()
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": None}])

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch") as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        mock_fcm.assert_not_called()

    def test_empty_tokens_does_not_raise(self):
        """Empty tokens case must not raise any exception."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate()
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": []}])

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch"), \
             patch("notifications.reminder._send_fcm_multicast"):
            # Should not raise
            send_booking_reminder(booking)

    def test_empty_tokens_logged(self, caplog):
        """Empty fcm_tokens must generate a log entry."""
        import logging
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": []}])

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch"), \
             patch("notifications.reminder._send_fcm_multicast"):
            with caplog.at_level(logging.INFO, logger="notifications.reminder"):
                send_booking_reminder(booking)

        # Some log entry about skipping must exist
        log_text = " ".join(caplog.messages)
        assert any(
            keyword in log_text.lower()
            for keyword in ["skip", "no token", "empty", "fcm_token"]
        ), f"Expected skip log. Got: {caplog.messages}"


# ---------------------------------------------------------------------------
# grava-52bc.3.7: Failed FCM send — retry once, then log and continue
# ---------------------------------------------------------------------------

class TestFCMRetry:
    """Failed FCM send retried once; permanent failure logged, continue (grava-52bc.3.7)."""

    def test_fcm_retried_once_on_first_failure(self):
        """If FCM fails on first try, it must be retried exactly once."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        fail_count = {"n": 0}

        def flaky_fcm(*args, **kwargs):
            if fail_count["n"] == 0:
                fail_count["n"] += 1
                raise Exception("FCM transient error")
            # second call succeeds (no exception)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast", side_effect=flaky_fcm) as mock_fcm:
            send_booking_reminder(booking)

        assert mock_fcm.call_count == 2, (
            f"FCM must be called exactly 2 times (first try + retry). Got {mock_fcm.call_count}"
        )

    def test_fcm_permanent_failure_does_not_raise(self):
        """If both FCM attempts fail, the function must not raise."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate()
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch") as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast",
                   side_effect=Exception("FCM always fails")):
            # Must not raise
            send_booking_reminder(booking)

        # On permanent failure, reminder_sent must NOT be updated
        mock_patch.assert_not_called()

    def test_fcm_permanent_failure_logged(self, caplog):
        """Permanent FCM failure must be logged as an error."""
        import logging
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch"), \
             patch("notifications.reminder._send_fcm_multicast",
                   side_effect=Exception("FCM always fails")):
            with caplog.at_level(logging.ERROR, logger="notifications.reminder"):
                send_booking_reminder(booking)

        assert len(caplog.records) > 0, "Must log an error on permanent FCM failure"
        assert any(
            r.levelno >= logging.ERROR for r in caplog.records
        ), "At least one ERROR-level log must exist"

    def test_fcm_retry_succeeds_reminder_sent_updated(self):
        """If first FCM fails but retry succeeds, reminder_sent must still be set."""
        from notifications.reminder import send_booking_reminder

        booking = _reminder_candidate(booking_id=_BOOKING_ID)
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        fail_count = {"n": 0}

        def flaky_fcm(*args, **kwargs):
            if fail_count["n"] == 0:
                fail_count["n"] += 1
                raise Exception("Transient FCM error")

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp) as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast", side_effect=flaky_fcm):
            send_booking_reminder(booking)

        mock_patch.assert_called_once()
        body = mock_patch.call_args.kwargs.get("json") or {}
        assert body.get("reminder_sent") is True


# ---------------------------------------------------------------------------
# grava-52bc.3.8: Series occurrences treated identically
# ---------------------------------------------------------------------------

class TestSeriesOccurrences:
    """Series bookings are treated identically (grava-52bc.3.8)."""

    def test_series_booking_sends_reminder(self):
        """A booking with booking_series_id set receives a reminder like any other."""
        from notifications.reminder import send_booking_reminder

        _series_id = str(uuid.uuid4())
        booking = _reminder_candidate(
            booking_id=_SERIES_BOOKING_ID,
            booking_series_id=_series_id,
        )
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp) as mock_patch, \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        mock_fcm.assert_called_once()
        mock_patch.assert_called_once()

    def test_series_booking_deep_link_uses_booking_id(self):
        """deep_link for series occurrence must use booking id (not series id)."""
        from notifications.reminder import send_booking_reminder

        _series_id = str(uuid.uuid4())
        booking = _reminder_candidate(
            booking_id=_SERIES_BOOKING_ID,
            booking_series_id=_series_id,
        )
        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            send_booking_reminder(booking)

        call_kwargs = mock_fcm.call_args.kwargs
        data = call_kwargs.get("data") or mock_fcm.call_args.args[3]
        assert _SERIES_BOOKING_ID in data.get("deep_link", ""), (
            "deep_link for series occurrence must contain booking_id"
        )
        # Series id should not be the primary identifier in the deep link
        assert data["booking_id"] == _SERIES_BOOKING_ID

    def test_multiple_candidates_all_processed(self):
        """When multiple reminder candidates exist, all receive reminders."""
        from notifications.reminder import process_booking_reminders

        booking1 = _reminder_candidate(booking_id=_BOOKING_ID, user_id=_USER_ID)
        booking2 = _reminder_candidate(booking_id=_BOOKING_ID_2, user_id=_USER_ID)
        candidates = [booking1, booking2]

        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.fetch_reminder_candidates", return_value=candidates), \
             patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast") as mock_fcm:
            process_booking_reminders()

        assert mock_fcm.call_count == 2, (
            f"FCM must be sent for each candidate. Got {mock_fcm.call_count}"
        )

    def test_one_failure_does_not_stop_others(self):
        """If processing one candidate fails, remaining candidates still processed."""
        from notifications.reminder import process_booking_reminders

        booking1 = _reminder_candidate(booking_id=_BOOKING_ID, user_id=_USER_ID)
        booking2 = _reminder_candidate(booking_id=_BOOKING_ID_2, user_id=_USER_ID)
        candidates = [booking1, booking2]

        call_count = {"n": 0}

        def partial_fail_fcm(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise Exception("First one fails always")

        user_resp = _ok_resp([{"id": _USER_ID, "fcm_tokens": ["tok1"]}])
        patch_resp = _ok_resp([], status=200)

        with patch("notifications.reminder.fetch_reminder_candidates", return_value=candidates), \
             patch("notifications.reminder.requests.get", return_value=user_resp), \
             patch("notifications.reminder.requests.patch", return_value=patch_resp), \
             patch("notifications.reminder._send_fcm_multicast", side_effect=partial_fail_fcm):
            # Must not raise
            process_booking_reminders()

        # Should have been attempted for both bookings (at least 2 FCM calls)
        assert call_count["n"] >= 2


# ---------------------------------------------------------------------------
# Management command integration
# ---------------------------------------------------------------------------

class TestManagementCommand:
    """Management command send_booking_reminders exists and is callable."""

    def test_management_command_module_exists(self):
        """bookings/management/commands/send_booking_reminders.py must exist."""
        import os
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(
            base, "bookings", "management", "commands", "send_booking_reminders.py"
        )
        assert os.path.isfile(path), (
            "bookings/management/commands/send_booking_reminders.py missing"
        )

    def test_management_command_importable(self):
        """send_booking_reminders management command must be importable."""
        from bookings.management.commands.send_booking_reminders import Command
        assert hasattr(Command, "handle"), "Command must have a handle() method"

    def test_management_command_handle_calls_process(self):
        """Command.handle() must call process_booking_reminders."""
        from bookings.management.commands.send_booking_reminders import Command

        with patch("notifications.reminder.process_booking_reminders") as mock_process, \
             patch("bookings.management.commands.send_booking_reminders.process_booking_reminders",
                   mock_process):
            cmd = Command()
            cmd.handle()

        mock_process.assert_called_once()
