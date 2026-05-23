"""RLS policies for the bookings table.

Revision ID: 0004
Revises: 0001
Create Date: 2026-05-23

Covers task grava-ea77.2.4:

    `bookings`: SELECT by booking owner OR court owner;
                INSERT by authenticated player;
                UPDATE status by court owner.

Policy summary (Supabase/PostgreSQL RLS):
  - Enable RLS on bookings.
  - SELECT policy `bookings_select_owner`:
      authenticated users may read a booking if they are the booking owner
      (user_id = auth.uid()) OR the court owner
      (court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())).
  - INSERT policy `bookings_insert_player`:
      authenticated users may create a booking only for themselves
      (WITH CHECK: user_id = auth.uid()).
  - UPDATE policy `bookings_update_court_owner`:
      authenticated users may update a booking only when they own the
      court that the booking is for
      (USING: court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())).

Non-owners cannot read, create, or modify bookings for other users' courts —
the default-deny behaviour of RLS enforces this automatically once RLS is
enabled and no matching policy exists for unauthenticated / other-user access.

Service-role (background jobs / admin) bypasses RLS by default in Supabase, so
no explicit policy is required for it — see grava-ea77.2.10.

The downgrade() reverses all statements in inverse order.

Note: this migration uses down_revision = "0001" because the sibling RLS
migrations (0002 for courts, 0003 for courts auto_approve_single) may be
merged in a different order. The Alembic branch heads can be resolved into a
linear chain when the PR is integrated.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on bookings and create access-control policies."""
    # Enable RLS — once enabled, default-deny applies for all roles until a
    # policy explicitly permits the operation.
    op.execute("ALTER TABLE bookings ENABLE ROW LEVEL SECURITY")

    # SELECT: a user may read a booking if they are the booking owner OR the
    # owner of the court the booking is attached to.
    op.execute(
        """
        CREATE POLICY bookings_select_owner
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

    # INSERT: authenticated players may create bookings, but only on their own
    # behalf (WITH CHECK ensures user_id = auth.uid() so a player cannot create
    # a booking attributed to another user).
    op.execute(
        """
        CREATE POLICY bookings_insert_player
        ON bookings
        FOR INSERT
        TO authenticated
        WITH CHECK (user_id = auth.uid())
        """
    )

    # UPDATE: only the court owner may update a booking (e.g. to change status
    # from pending → confirmed / cancelled / completed).  The USING clause
    # restricts which rows can be targeted; there is no WITH CHECK because the
    # court ownership of the booking itself is not changing.
    op.execute(
        """
        CREATE POLICY bookings_update_court_owner
        ON bookings
        FOR UPDATE
        TO authenticated
        USING (
            court_id IN (
                SELECT id FROM courts WHERE owner_id = auth.uid()
            )
        )
        """
    )


def downgrade() -> None:
    """Drop bookings RLS policies and disable RLS."""
    op.execute("DROP POLICY IF EXISTS bookings_update_court_owner ON bookings")
    op.execute("DROP POLICY IF EXISTS bookings_insert_player ON bookings")
    op.execute("DROP POLICY IF EXISTS bookings_select_owner ON bookings")
    op.execute("ALTER TABLE bookings DISABLE ROW LEVEL SECURITY")
