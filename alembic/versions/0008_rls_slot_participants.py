"""RLS policies for the slot_participants table.

Revision ID: 0008
Revises: 0001
Create Date: 2026-05-24

Covers task grava-ea77.2.8:

    `slot_participants`: SELECT by slot owner or participant;
                         INSERT by slot owner.

Policy summary (Supabase/PostgreSQL RLS):
  - Enable RLS on slot_participants.
  - SELECT policy `slot_participants_select`:
      authenticated users may read a row if they are the participant
      (user_id = auth.uid()) OR the slot owner
      (slot_id IN (SELECT s.id FROM slots s
                   JOIN courts c ON c.id = s.court_id
                   WHERE c.owner_id = auth.uid())).
  - INSERT policy `slot_participants_insert_slot_owner`:
      only the slot owner (court owner via slots -> courts) may add
      participants.  WITH CHECK ensures slot_id maps to a slot on a court
      owned by auth.uid().

"Slot owner" is defined as the owner of the court to which the slot belongs,
because the `slots` table has no direct owner column — ownership is tracked on
the `courts` table via courts.owner_id.

Regular participants can see their own rows (and all rows for slots they are
in) via the participant arm of the SELECT policy. Non-participants and
non-owners are blocked by the default-deny posture of RLS.

Service-role (background jobs / admin) bypasses RLS by default in Supabase, so
no explicit policy is required for it — see grava-ea77.2.10.

Note: this migration uses down_revision = "0001" because the sibling RLS
migrations (0002–0010) may be merged in a different order. The Alembic branch
heads can be resolved into a linear chain when the PRs are integrated.

The downgrade() reverses all statements in inverse order.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on slot_participants and create access-control policies."""
    # Enable RLS — once enabled, default-deny applies for all roles until a
    # policy explicitly permits the operation.
    op.execute("ALTER TABLE slot_participants ENABLE ROW LEVEL SECURITY")

    # SELECT: a user may read a slot_participants row if they are:
    #   a) the participant in that row (user_id = auth.uid()), OR
    #   b) the slot owner, i.e. the owner of the court the slot belongs to
    #      (slot_id -> slots -> courts -> owner_id = auth.uid()).
    op.execute(
        """
        CREATE POLICY slot_participants_select
        ON slot_participants
        FOR SELECT
        TO authenticated
        USING (
            user_id = auth.uid()
            OR slot_id IN (
                SELECT s.id
                FROM slots s
                JOIN courts c ON c.id = s.court_id
                WHERE c.owner_id = auth.uid()
            )
        )
        """
    )

    # INSERT: only the slot owner (court owner via slots -> courts) may add
    # participants.  WITH CHECK validates the new row: the slot being referenced
    # must belong to a court owned by auth.uid().
    op.execute(
        """
        CREATE POLICY slot_participants_insert_slot_owner
        ON slot_participants
        FOR INSERT
        TO authenticated
        WITH CHECK (
            slot_id IN (
                SELECT s.id
                FROM slots s
                JOIN courts c ON c.id = s.court_id
                WHERE c.owner_id = auth.uid()
            )
        )
        """
    )


def downgrade() -> None:
    """Drop slot_participants RLS policies and disable RLS."""
    op.execute(
        "DROP POLICY IF EXISTS slot_participants_insert_slot_owner ON slot_participants"
    )
    op.execute(
        "DROP POLICY IF EXISTS slot_participants_select ON slot_participants"
    )
    op.execute("ALTER TABLE slot_participants DISABLE ROW LEVEL SECURITY")
