"""Enable Supabase Realtime on `bookings` table (grava-8038.2 / BCORE-041).

Revision ID: 0011
Revises: 0001
Create Date: 2026-05-26

Covers grava-8038.2 (BCORE-041) — Supabase Realtime: booking status.

Purpose
-------
Flutter clients need to receive live `UPDATE` events when a booking's `status`
field changes (e.g. pending → confirmed, pending → cancelled).  This migration
wires up the database-side prerequisites:

1. REPLICA IDENTITY FULL on `bookings`:
   Supabase Realtime broadcasts change events that include both the old and the
   new row values.  By default PostgreSQL only includes the primary key in the
   WAL for UPDATE/DELETE events (REPLICA IDENTITY DEFAULT).  Setting FULL
   ensures the entire old row is captured so clients can perform client-side
   record reconciliation.

2. Add `bookings` to the `supabase_realtime` publication:
   Supabase creates a logical-replication publication named `supabase_realtime`
   during project setup.  Tables must be explicitly added to this publication
   before change events flow to connected clients.

3. SELECT policy `bookings_select_player` (Realtime channel RLS):
   When a Flutter client opens a Realtime channel for the `bookings` table,
   Supabase evaluates the table's RLS policies to decide which row changes the
   client may receive.  This policy ensures that:
     • A player only receives events for their own bookings
       (user_id = auth.uid()).
     • A court owner receives events for all bookings on their courts
       (court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())).

   Note: migration 0004 already has an identical SELECT policy
   (`bookings_select_owner`) applied to the table.  Rather than altering that
   existing policy (which would cause a conflict with the migration that created
   it), we add a complementary named policy here that Supabase will use when
   evaluating Realtime channel subscriptions.  If the 0004 policy already
   covers the use-case, Supabase OR-combines all matching policies.

Downgrade
---------
• Drops the `bookings_select_player` policy (IF EXISTS — idempotent).
• Removes `bookings` from the `supabase_realtime` publication.
• Resets REPLICA IDENTITY back to DEFAULT.

Note: uses down_revision = "0001" so this migration sits on the same branch
as the other numbered migrations (0004, 0005, etc.) that also chain from 0001.
The Alembic branch heads can be linearised when all migrations land on main.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable Realtime on bookings and add a Realtime-scoped SELECT policy."""

    # ------------------------------------------------------------------
    # Step 1: REPLICA IDENTITY FULL
    # Ensures UPDATE/DELETE WAL events include the full old row so that
    # Realtime subscribers can reconcile their local state.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE bookings REPLICA IDENTITY FULL")

    # ------------------------------------------------------------------
    # Step 2: Add bookings to the supabase_realtime publication
    # Without this, no change events reach Realtime subscribers.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER PUBLICATION supabase_realtime ADD TABLE bookings"
    )

    # ------------------------------------------------------------------
    # Step 3: SELECT policy for Realtime channel access
    # Supabase uses the table's RLS policies to decide which row events
    # a connected client may receive.  This policy grants SELECT to:
    #   a) the booking owner (user_id = auth.uid())
    #   b) the court owner of the booking's court
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE POLICY bookings_select_player
        ON bookings
        FOR SELECT
        TO authenticated
        USING (
            user_id = auth.uid()
            OR court_id IN (
                SELECT id FROM courts WHERE owner_id = auth.uid()
            )
        )
        """
    )


def downgrade() -> None:
    """Drop the Realtime SELECT policy, remove from publication, reset REPLICA IDENTITY."""

    # Drop the SELECT policy added by this migration (idempotent).
    op.execute(
        "DROP POLICY IF EXISTS bookings_select_player ON bookings"
    )

    # Remove bookings from the supabase_realtime publication.
    op.execute(
        "ALTER PUBLICATION supabase_realtime DROP TABLE bookings"
    )

    # Reset REPLICA IDENTITY to the PostgreSQL default.
    op.execute("ALTER TABLE bookings REPLICA IDENTITY DEFAULT")
