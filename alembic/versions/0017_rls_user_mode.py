"""RLS user-mode support: policies + helper functions for user-JWT requests.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-29

Context
-------
User-facing endpoints now call PostgREST with the end user's JWT (RLS enforced)
instead of the secret/service-role key. Several operations that previously
relied on service-role bypass need explicit support so they keep working under
RLS:

  1. create_notification(...) — SECURITY DEFINER RPC. Notifications are routinely
     created for a *different* user (a player's booking notifies the court owner;
     an owner's approval notifies the requester). No ``user_id = auth.uid()``
     INSERT policy could ever permit that, so creation goes through a definer
     function, mirroring the existing FCM token RPCs (migration 0013).

  2. customers self policies — the customers table had RLS enabled (0002) but
     ZERO policies, so authenticated users could not even read their own row.
     Add SELECT/UPDATE scoped to ``id = auth.uid()`` (players/me, profile, avatar,
     location, booking player-info lookups).

  3. bookings player-cancel — bookings UPDATE was court-owner only (0004), which
     blocked a player cancelling their own booking (CAPP-052). Add a player
     UPDATE policy scoped to ``user_id = auth.uid()``.

  4. slots owner writes — slots had only SELECT policies (0010). Court owners need
     INSERT/UPDATE for slot creation, blocking/unblocking, manual bookings and
     recurrence generation. Scope to courts owned by ``auth.uid()``.

  5. slot status trigger — a player flipping a shared slot open→booked (and
     freeing it on cancel) cannot be expressed as a safe RLS policy without
     letting any user mutate any slot. Instead slot status is maintained as a
     derived consequence of booking state via a SECURITY DEFINER trigger. The
     views still issue best-effort slot PATCHes; under RLS those are no-ops for
     players and the trigger is the source of truth.

Known follow-ups (NOT covered here — see PR description):
  - Booking-series auto-creation of *missing* open slots by a player still needs
    a definer path (works today only when the owner has pre-generated the slots).
  - Storage avatar upload now uses the user JWT; the ``avatars`` bucket needs an
    RLS policy allowing an authenticated user to write under ``<auth.uid()>/``.
  - slot_join_requests / slot_participants read paths for the requesting player.
"""

from __future__ import annotations

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0017"
down_revision: str | None = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. create_notification — SECURITY DEFINER RPC (cross-user inserts)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION create_notification(
            p_user_id            uuid,
            p_title              text,
            p_body               text,
            p_related_booking_id uuid DEFAULT NULL,
            p_related_slot_id    uuid DEFAULT NULL,
            p_related_series_id  uuid DEFAULT NULL
        )
        RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            v_id uuid;
        BEGIN
            INSERT INTO notifications (
                user_id, title, body, read,
                related_booking_id, related_slot_id, related_series_id
            )
            VALUES (
                p_user_id, p_title, p_body, false,
                p_related_booking_id, p_related_slot_id, p_related_series_id
            )
            RETURNING id INTO v_id;
            RETURN v_id;
        END;
        $$
        """
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION create_notification("
        "uuid, text, text, uuid, uuid, uuid) TO authenticated"
    )

    # ------------------------------------------------------------------
    # 2. customers — self read/update (table had RLS on, no policies)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE POLICY customers_select_self
        ON customers
        FOR SELECT
        TO authenticated
        USING (id = auth.uid())
        """
    )
    op.execute(
        """
        CREATE POLICY customers_update_self
        ON customers
        FOR UPDATE
        TO authenticated
        USING (id = auth.uid())
        WITH CHECK (id = auth.uid())
        """
    )

    # ------------------------------------------------------------------
    # 3. bookings — player may update (cancel) their own booking
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE POLICY bookings_update_player_self
        ON bookings
        FOR UPDATE
        TO authenticated
        USING (user_id = auth.uid())
        WITH CHECK (user_id = auth.uid())
        """
    )

    # ------------------------------------------------------------------
    # 4. slots — court owner INSERT/UPDATE
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE POLICY slots_insert_court_owner
        ON slots
        FOR INSERT
        TO authenticated
        WITH CHECK (
            court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())
        )
        """
    )
    op.execute(
        """
        CREATE POLICY slots_update_court_owner
        ON slots
        FOR UPDATE
        TO authenticated
        USING (
            court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())
        )
        WITH CHECK (
            court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())
        )
        """
    )

    # ------------------------------------------------------------------
    # 5. slot status follows booking state (SECURITY DEFINER trigger)
    #    - new booking (pending|confirmed) marks an open slot booked
    #    - a booking moving to cancelled frees its slot back to open
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_slot_status_from_booking()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            IF (TG_OP = 'INSERT') THEN
                IF NEW.status IN ('pending', 'confirmed') THEN
                    UPDATE slots SET status = 'booked'
                    WHERE id = NEW.slot_id AND status = 'open';
                END IF;
            ELSIF (TG_OP = 'UPDATE') THEN
                IF NEW.status = 'cancelled' AND OLD.status <> 'cancelled' THEN
                    UPDATE slots SET status = 'open'
                    WHERE id = NEW.slot_id AND status = 'booked';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sync_slot_status_from_booking
        AFTER INSERT OR UPDATE OF status ON bookings
        FOR EACH ROW
        EXECUTE FUNCTION sync_slot_status_from_booking()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_sync_slot_status_from_booking ON bookings"
    )
    op.execute("DROP FUNCTION IF EXISTS sync_slot_status_from_booking()")

    op.execute("DROP POLICY IF EXISTS slots_update_court_owner ON slots")
    op.execute("DROP POLICY IF EXISTS slots_insert_court_owner ON slots")
    op.execute("DROP POLICY IF EXISTS bookings_update_player_self ON bookings")
    op.execute("DROP POLICY IF EXISTS customers_update_self ON customers")
    op.execute("DROP POLICY IF EXISTS customers_select_self ON customers")

    op.execute(
        "DROP FUNCTION IF EXISTS create_notification("
        "uuid, text, text, uuid, uuid, uuid)"
    )
