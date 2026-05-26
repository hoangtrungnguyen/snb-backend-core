"""Enable Supabase Realtime on `notifications` table (grava-8038.3 / BCORE-042).

Revision ID: 0012
Revises: 0001
Create Date: 2026-05-26

Covers grava-8038.3 (BCORE-042) — Supabase Realtime: notifications.

Purpose
-------
Flutter clients need to receive live INSERT events when a new notification row
is written by the backend (or a Postgres trigger), so that the unread badge
count can be incremented and a toast shown without polling.

This migration wires up the database-side prerequisites:

1. REPLICA IDENTITY FULL on `notifications`:
   Supabase Realtime broadcasts change events that include both the old and the
   new row values.  By default PostgreSQL only includes the primary key in the
   WAL for UPDATE/DELETE events (REPLICA IDENTITY DEFAULT).  Setting FULL
   ensures the entire old row is captured so clients can reconcile their local
   state (e.g. detecting that read_at changed from NULL to a timestamp when the
   notification centre is opened — grava-8038.3.4).

2. Add `notifications` to the `supabase_realtime` publication:
   Supabase creates a logical-replication publication named `supabase_realtime`
   during project setup.  Tables must be explicitly added to this publication
   before change events flow to connected clients.

No new RLS policies are needed.
   Migration 0005 already created the following policies on `notifications`:
     • notifications_select_owner  — SELECT: user_id = auth.uid()
     • notifications_update_owner  — UPDATE: user_id = auth.uid()
   Supabase Realtime re-uses these existing table RLS policies to decide which
   row events to broadcast to each subscriber.  An authenticated client whose
   JWT contains uid=X will only receive events for rows where user_id = X.

Client subscription pattern (Flutter, for reference — grava-8038.3.2):
   supabase.from('notifications')
     .on('INSERT', handler)
     .eq('user_id', authUid)
     .subscribe()

Downgrade
---------
• Removes `notifications` from the `supabase_realtime` publication.
• Resets REPLICA IDENTITY back to DEFAULT.
• Does NOT touch the RLS policies (managed by migration 0005).

Note: uses down_revision = "0001" so this migration sits on the same branch
as the other numbered migrations (0005, 0010, 0011, etc.) that also chain
from 0001.  The Alembic branch heads can be linearised when all migrations
land on main.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable Realtime on notifications: REPLICA IDENTITY FULL + publication."""

    # ------------------------------------------------------------------
    # Step 1: REPLICA IDENTITY FULL
    # Ensures UPDATE/DELETE WAL events include the full old row so that
    # Realtime subscribers can reconcile their local notification state,
    # including detecting when read_at is stamped (grava-8038.3.4).
    # This must be set before adding the table to the publication.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE notifications REPLICA IDENTITY FULL")

    # ------------------------------------------------------------------
    # Step 2: Add notifications to the supabase_realtime publication
    # Without this, no change events reach Realtime subscribers.
    # Existing RLS policies from migration 0005 automatically gate which
    # rows each authenticated subscriber receives — no new policy needed.
    # ------------------------------------------------------------------
    op.execute(
        "ALTER PUBLICATION supabase_realtime ADD TABLE notifications"
    )


def downgrade() -> None:
    """Remove notifications from supabase_realtime and reset REPLICA IDENTITY."""

    # Remove notifications from the supabase_realtime publication.
    op.execute(
        "ALTER PUBLICATION supabase_realtime DROP TABLE notifications"
    )

    # Reset REPLICA IDENTITY to the PostgreSQL default.
    op.execute("ALTER TABLE notifications REPLICA IDENTITY DEFAULT")
