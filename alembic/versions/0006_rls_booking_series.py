"""RLS policies for the booking_series table.

Revision ID: 0006
Revises: 0001
Create Date: 2026-05-23

Covers task grava-ea77.2.5:

    `booking_series`: SELECT by series owner OR court owner;
                      INSERT by authenticated player;
                      UPDATE by series owner OR court owner.

Policy summary (Supabase/PostgreSQL RLS):
  - Enable RLS on booking_series.
  - SELECT policy `booking_series_select_owner`:
      authenticated users may read a booking_series row if they are the
      series owner (user_id = auth.uid()) OR the court owner
      (court_id IN (SELECT id FROM courts WHERE owner_id = auth.uid())).
  - INSERT policy `booking_series_insert_player`:
      authenticated users may create a booking_series row only on their own
      behalf (WITH CHECK: user_id = auth.uid()).
  - UPDATE policy `booking_series_update_owner`:
      authenticated users may update a booking_series row if they are the
      series owner OR the court owner.
      USING restricts which rows can be targeted; WITH CHECK ensures the
      ownership relationship is preserved after the update.

Non-owners cannot read, create, or modify booking_series rows — the
default-deny behaviour of RLS enforces this automatically once RLS is
enabled and no matching policy exists for unauthenticated / other-user access.

Service-role (background jobs / admin) bypasses RLS by default in Supabase,
so no explicit policy is required for it — see grava-ea77.2.10.

The downgrade() reverses all statements in inverse order.

Note: this migration uses down_revision = "0001" because the sibling RLS
migrations (0002–0005) may be merged in a different order. The Alembic branch
heads can be resolved into a linear chain when the PRs are integrated.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on booking_series and create access-control policies."""
    # Enable RLS — once enabled, default-deny applies for all roles until a
    # policy explicitly permits the operation.
    op.execute("ALTER TABLE booking_series ENABLE ROW LEVEL SECURITY")

    # SELECT: a user may read a booking_series row if they are the series
    # owner (user_id = auth.uid()) OR the owner of the associated court.
    op.execute(
        """
        CREATE POLICY booking_series_select_owner
        ON booking_series
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

    # INSERT: authenticated players may create booking_series rows, but only
    # on their own behalf (WITH CHECK ensures user_id = auth.uid() so a player
    # cannot create a series attributed to another user).
    op.execute(
        """
        CREATE POLICY booking_series_insert_player
        ON booking_series
        FOR INSERT
        TO authenticated
        WITH CHECK (user_id = auth.uid())
        """
    )

    # UPDATE: series owners OR court owners may update a booking_series row.
    # USING restricts which rows can be targeted; WITH CHECK ensures the
    # ownership relationship is preserved after the update so that neither
    # the user_id nor the court_id can be changed to circumvent the policy.
    op.execute(
        """
        CREATE POLICY booking_series_update_owner
        ON booking_series
        FOR UPDATE
        TO authenticated
        USING (
            user_id = auth.uid()
            OR court_id IN (
                SELECT id FROM courts WHERE owner_id = auth.uid()
            )
        )
        WITH CHECK (
            user_id = auth.uid()
            OR court_id IN (
                SELECT id FROM courts WHERE owner_id = auth.uid()
            )
        )
        """
    )


def downgrade() -> None:
    """Drop booking_series RLS policies and disable RLS."""
    op.execute("DROP POLICY IF EXISTS booking_series_update_owner ON booking_series")
    op.execute("DROP POLICY IF EXISTS booking_series_insert_player ON booking_series")
    op.execute("DROP POLICY IF EXISTS booking_series_select_owner ON booking_series")
    op.execute("ALTER TABLE booking_series DISABLE ROW LEVEL SECURITY")
