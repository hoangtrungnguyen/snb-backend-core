"""Supabase Realtime for `slots` + RLS for slot availability.

Revision ID: 0010
Revises: 0001
Create Date: 2026-05-26

Covers story grava-8038.1 (BCORE-040) — Supabase Realtime: slot availability.

Tasks implemented:
  grava-8038.1.1  Enable Realtime on `slots` table (publication: supabase_realtime)
  grava-8038.1.5  RLS ensures clients only receive rows for
                  status IN ('open', 'booked') on 'approved' courts

Architecture note:
  Supabase Realtime works by streaming Postgres logical-replication WAL events
  to subscribed clients over websockets.  A table must be added to the
  `supabase_realtime` publication for its change events to be broadcast.  The
  Django/DRF API writes to the `slots` table as normal; no additional Django
  code is required — Realtime delivery is automatic once the publication is
  configured.

Policy summary (Supabase/PostgreSQL RLS):

  1. Enable RLS on slots (default-deny until a policy permits the operation).

  2. SELECT policy `slots_select_available`:
       anon + authenticated may read a slot row only when both conditions hold:
         a) slots.status IN ('open', 'booked')
            — 'blocked' and 'maintenance' rows are invisible to clients.
         b) The slot's court has courts.status = 'approved'
            — clients never receive rows for pending / suspended courts.
       This policy is what Supabase Realtime evaluates to decide whether to
       broadcast an UPDATE event to a subscriber.  If the updated row no longer
       matches USING, the Realtime engine sends a DELETE event to the client
       (the "invisible" tombstone behaviour).

  3. SELECT policy `slots_select_owner`:
       authenticated court owners may read ALL slots on their own courts
       regardless of slot.status or courts.status, so the owner dashboard
       always shows the full picture.

Service-role (background jobs / admin) bypasses RLS by default in Supabase, so
no explicit policy is needed for it.

Note: down_revision = "0001" because the sibling RLS migrations (0002–0009)
may be merged in a different order.  The Alembic branch heads can be resolved
into a linear chain when the PRs are integrated.

The downgrade() reverses all statements in inverse order, using IF EXISTS for
idempotency.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add slots to supabase_realtime publication and configure RLS."""

    # ------------------------------------------------------------------
    # 1. Enable Realtime: add slots to the supabase_realtime publication
    #    (grava-8038.1.1)
    # ------------------------------------------------------------------
    op.execute("ALTER PUBLICATION supabase_realtime ADD TABLE slots")

    # ------------------------------------------------------------------
    # 2. Enable RLS on slots — default-deny for all non-service-role roles
    #    (prerequisite for the policies below)
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE slots ENABLE ROW LEVEL SECURITY")

    # ------------------------------------------------------------------
    # 3. SELECT policy: public slot availability (Realtime feed)
    #    (grava-8038.1.5)
    #
    #    Condition: slot.status IN ('open', 'booked')
    #               AND the slot's court has courts.status = 'approved'
    #
    #    Target roles: anon + authenticated
    #      - anon   : for unauthenticated Realtime subscribers / map views
    #      - authenticated: for logged-in players filtering by nearby courts
    #
    #    Supabase Realtime respects RLS: an UPDATE that leaves the row
    #    still matching USING produces an UPDATE event; one that causes it
    #    to no longer match produces a DELETE event for the subscriber.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE POLICY slots_select_available
        ON slots
        FOR SELECT
        TO anon, authenticated
        USING (
            status IN ('open', 'booked')
            AND court_id IN (
                SELECT id FROM courts WHERE status = 'approved'
            )
        )
        """
    )

    # ------------------------------------------------------------------
    # 4. SELECT policy: court owner sees all their slots
    #
    #    A court owner must be able to read ALL slots on their courts
    #    (including 'blocked', 'maintenance', slots on pending/suspended
    #    courts) so the owner dashboard stays fully populated.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE POLICY slots_select_owner
        ON slots
        FOR SELECT
        TO authenticated
        USING (
            court_id IN (
                SELECT id FROM courts WHERE owner_id = auth.uid()
            )
        )
        """
    )


def downgrade() -> None:
    """Remove slots from supabase_realtime, drop RLS policies, disable RLS."""
    op.execute("DROP POLICY IF EXISTS slots_select_owner ON slots")
    op.execute("DROP POLICY IF EXISTS slots_select_available ON slots")
    op.execute("ALTER TABLE slots DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER PUBLICATION supabase_realtime DROP TABLE slots")
