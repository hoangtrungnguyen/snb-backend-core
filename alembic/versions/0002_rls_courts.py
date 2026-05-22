"""RLS policies for the courts table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-22

Covers task grava-ea77.2.1:

    `courts`: SELECT public for `status = approved`;
              INSERT/UPDATE/DELETE only `owner_id = auth.uid()`.

Policy summary (Supabase/PostgreSQL RLS):
  - Enable RLS on courts.
  - SELECT policy `courts_select_public_approved`:
      anyone (anon + authenticated) may read rows where status = 'approved'.
  - SELECT policy `courts_select_owner`:
      authenticated users may read all of their own rows regardless of status
      (so a pending/suspended owner can still see their own court).
  - INSERT policy `courts_insert_owner`:
      authenticated users may insert rows only when owner_id = auth.uid().
  - UPDATE policy `courts_update_owner`:
      authenticated users may update only their own rows
      (USING + WITH CHECK both pin owner_id to auth.uid()).
  - DELETE policy `courts_delete_owner`:
      authenticated users may delete only their own rows.

Service-role (background jobs / admin) bypasses RLS by default in Supabase, so
no explicit policy is required for it — see grava-ea77.2.10.

The downgrade reverses these statements in inverse order.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on courts and create owner-scoped policies."""
    # Enable RLS — once enabled, default-deny applies until a policy permits.
    op.execute("ALTER TABLE courts ENABLE ROW LEVEL SECURITY")

    # SELECT: anyone may read approved courts (public listing).
    op.execute(
        """
        CREATE POLICY courts_select_public_approved
        ON courts
        FOR SELECT
        TO anon, authenticated
        USING (status = 'approved')
        """
    )

    # SELECT: an owner may always read their own court even when pending /
    # suspended. Without this, a court owner whose listing is still pending
    # could not see it.
    op.execute(
        """
        CREATE POLICY courts_select_owner
        ON courts
        FOR SELECT
        TO authenticated
        USING (owner_id = auth.uid())
        """
    )

    # INSERT: authenticated users may create a court only for themselves.
    op.execute(
        """
        CREATE POLICY courts_insert_owner
        ON courts
        FOR INSERT
        TO authenticated
        WITH CHECK (owner_id = auth.uid())
        """
    )

    # UPDATE: owner may update their own court; cannot reassign ownership
    # because WITH CHECK also pins owner_id = auth.uid().
    op.execute(
        """
        CREATE POLICY courts_update_owner
        ON courts
        FOR UPDATE
        TO authenticated
        USING (owner_id = auth.uid())
        WITH CHECK (owner_id = auth.uid())
        """
    )

    # DELETE: owner may delete their own court.
    op.execute(
        """
        CREATE POLICY courts_delete_owner
        ON courts
        FOR DELETE
        TO authenticated
        USING (owner_id = auth.uid())
        """
    )


def downgrade() -> None:
    """Drop courts RLS policies and disable RLS."""
    op.execute("DROP POLICY IF EXISTS courts_delete_owner ON courts")
    op.execute("DROP POLICY IF EXISTS courts_update_owner ON courts")
    op.execute("DROP POLICY IF EXISTS courts_insert_owner ON courts")
    op.execute("DROP POLICY IF EXISTS courts_select_owner ON courts")
    op.execute("DROP POLICY IF EXISTS courts_select_public_approved ON courts")
    op.execute("ALTER TABLE courts DISABLE ROW LEVEL SECURITY")
