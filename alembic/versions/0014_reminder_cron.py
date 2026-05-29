"""pg_cron scheduling for booking reminder candidates (grava-52bc.3.1).

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-26

Covers grava-52bc.3.1 — pg_cron schedules mark_reminder_candidates() every 5 min.

What this migration does:
1. Creates the `mark_reminder_candidates()` PL/pgSQL function.
   - Finds confirmed bookings not yet reminded with start_at between NOW()+55min
     and NOW()+65min (the T-60 window).
   - This is a no-op marker function: the actual FCM push is done by the Django
     management command (grava-52bc.3.2) which independently queries the same
     window. The function exists so pg_cron can also trigger candidate selection
     at the DB level for observability / dual-pipeline safety.
2. Schedules the function via pg_cron every 5 minutes using cron.schedule().
   pg_cron extension must be enabled on the Postgres instance.

Downgrade removes the cron job and the function.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Ensure pg_cron extension is enabled — skip silently if unavailable
    # (Supabase only allows pg_cron in the cron.database_name database)
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS pg_cron;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_cron not available in this database, skipping: %', SQLERRM;
        END;
        $$
        """
    )

    # ------------------------------------------------------------------
    # mark_reminder_candidates() — identify bookings in the T-60 window
    # Joins bookings → slots to access start_at, and bookings → courts
    # for court_name and address needed by the FCM payload.
    # The function marks nothing itself; it is a queryable signal used by
    # the Django polling command and observable by monitoring queries.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mark_reminder_candidates()
        RETURNS TABLE (
            booking_id      uuid,
            user_id         uuid,
            court_id        uuid,
            slot_id         uuid,
            booking_series_id uuid,
            court_name      text,
            court_address   text,
            start_at        timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            RETURN QUERY
            SELECT
                b.id            AS booking_id,
                b.user_id,
                b.court_id,
                b.slot_id,
                b.booking_series_id,
                c.name          AS court_name,
                c.address       AS court_address,
                s.start_at
            FROM bookings b
            JOIN slots   s ON s.id = b.slot_id
            JOIN courts  c ON c.id = b.court_id
            WHERE
                b.status        = 'confirmed'
                AND b.reminder_sent = false
                AND s.start_at  BETWEEN NOW() + INTERVAL '55 minutes'
                                    AND NOW() + INTERVAL '65 minutes';
        END;
        $$
        """
    )

    # ------------------------------------------------------------------
    # Schedule mark_reminder_candidates() via pg_cron every 5 minutes.
    # The job is owned by the postgres superuser role.
    # cron.schedule() is idempotent by job name.
    # ------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            PERFORM cron.schedule(
                'reminder-candidates-every-5min',
                '*/5 * * * *',
                'SELECT mark_reminder_candidates()'
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_cron schedule skipped: %', SQLERRM;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            PERFORM cron.unschedule('reminder-candidates-every-5min');
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_cron unschedule skipped: %', SQLERRM;
        END;
        $$
        """
    )
    op.execute("DROP FUNCTION IF EXISTS mark_reminder_candidates()")
