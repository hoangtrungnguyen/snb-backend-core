"""RLS policies for the notifications table.

Revision ID: 0005
Revises: 0001
Create Date: 2026-05-23

Covers task grava-ea77.2.6:

    `notifications`: SELECT/UPDATE only `user_id = auth.uid()`

Policy summary (Supabase/PostgreSQL RLS):
  - Enable RLS on notifications.
  - SELECT policy `notifications_select_owner`:
      authenticated users may read a notification only if they own it
      (USING: user_id = auth.uid()).
  - UPDATE policy `notifications_update_owner`:
      authenticated users may update a notification only if they own it
      (USING: user_id = auth.uid()).

INSERT and DELETE are not permitted via RLS — the default-deny behaviour of
RLS blocks any operation for which no policy exists once RLS is enabled.
Non-owners cannot read or modify other users' notifications.

Service-role (background jobs / admin) bypasses RLS by default in Supabase, so
no explicit policy is required for it — see grava-ea77.2.10.

The downgrade() reverses all statements in inverse order.

Note: this migration uses down_revision = "0001" because sibling RLS
migrations (0002, 0003, 0004) may be merged in a different order. The Alembic
branch heads can be resolved into a linear chain when the PRs are integrated.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on notifications and create access-control policies."""
    # Enable RLS — once enabled, default-deny applies for all roles until a
    # policy explicitly permits the operation.
    op.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY")

    # SELECT: a user may read a notification only if they are the owner
    # (user_id = auth.uid()).
    op.execute(
        """
        CREATE POLICY notifications_select_owner
        ON notifications
        FOR SELECT
        TO authenticated
        USING (user_id = auth.uid())
        """
    )

    # UPDATE: a user may update a notification only if they are the owner
    # (user_id = auth.uid()).  This allows marking notifications as read,
    # which is the primary update use-case for this table.
    op.execute(
        """
        CREATE POLICY notifications_update_owner
        ON notifications
        FOR UPDATE
        TO authenticated
        USING (user_id = auth.uid())
        """
    )


def downgrade() -> None:
    """Drop notifications RLS policies and disable RLS."""
    op.execute("DROP POLICY IF EXISTS notifications_update_owner ON notifications")
    op.execute("DROP POLICY IF EXISTS notifications_select_owner ON notifications")
    op.execute("ALTER TABLE notifications DISABLE ROW LEVEL SECURITY")
