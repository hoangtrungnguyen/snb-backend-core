"""
Django management command: send_booking_reminders (grava-52bc.3.2).

Polls Supabase every invocation for confirmed bookings with start_at in the
T-60 window (now+55min to now+65min, reminder_sent=false) and sends FCM
push notifications via the notifications.reminder module.

Intended to be invoked by an external cron (system cron, Celery beat, or
a Kubernetes CronJob) every 5 minutes to match the pg_cron schedule in
migration 0014.

Usage:
    python manage.py send_booking_reminders
"""

import logging

from django.core.management.base import BaseCommand

from notifications.reminder import process_booking_reminders

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Send FCM push reminders for bookings starting in ~60 minutes "
        "(grava-52bc.3: Push booking reminder)."
    )

    def handle(self, *args, **options):
        logger.info("send_booking_reminders: starting")
        self.stdout.write("send_booking_reminders: polling for reminder candidates…")

        try:
            process_booking_reminders()
            self.stdout.write(self.style.SUCCESS("send_booking_reminders: done"))
        except Exception as exc:  # noqa: BLE001
            logger.error("send_booking_reminders: unhandled error: %s", exc)
            self.stderr.write(
                self.style.ERROR(f"send_booking_reminders: failed — {exc}")
            )
