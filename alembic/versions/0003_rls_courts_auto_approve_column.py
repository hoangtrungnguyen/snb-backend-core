"""Column-level access control on ``courts.auto_approve_single``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-22

Covers task grava-ea77.2.2:

    ``courts.auto_approve_single`` — SELECT/UPDATE only ``owner_id = auth.uid()``
    (linked to OWNER-44).

Rationale
=========
The court-wide RLS policies created in migration 0002 (``courts_select_public_approved``,
``courts_update_owner``, ...) operate at the row level: any anonymous reader can
see the *entire* row when ``status = 'approved'``, and any authenticated owner
can update *any* column on their own row. ``auto_approve_single`` is an
owner-only preference: anonymous / non-owner readers should not be able to see
it, and the row-level UPDATE policy is sufficient for owner writes.

PostgreSQL does not let RLS policies project columns directly, but
column-level ``GRANT/REVOKE`` does. We therefore:

  1. Revoke SELECT / UPDATE on ``courts.auto_approve_single`` from PUBLIC and
     from the Supabase ``anon`` role (anonymous readers).
  2. Re-grant SELECT / UPDATE on the column to ``authenticated``. The
     ``courts_update_owner`` row-level policy (from 0002) then further narrows
     UPDATEs to ``owner_id = auth.uid()``, and ``courts_select_owner``
     restricts SELECTs to the owner's own rows.

Net effect: only the row owner can SELECT or UPDATE this column.

Service-role bypasses RLS but also bypasses column GRANTs by virtue of its
``BYPASSRLS`` attribute combined with table-level ownership — so background
jobs continue to work.

Idempotency
===========
``REVOKE`` and ``GRANT`` are idempotent in PostgreSQL — re-running this
migration is safe.

Downgrade
=========
``downgrade()`` re-grants the column to PUBLIC / anon (the default before
this migration applied), restoring the pre-migration permission set.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Lock ``auto_approve_single`` to the row owner via column-level GRANTs."""
    # Strip default access from PUBLIC / anon.
    op.execute(
        "REVOKE SELECT (auto_approve_single) ON courts FROM PUBLIC"
    )
    op.execute(
        "REVOKE UPDATE (auto_approve_single) ON courts FROM PUBLIC"
    )
    op.execute(
        "REVOKE SELECT (auto_approve_single) ON courts FROM anon"
    )
    op.execute(
        "REVOKE UPDATE (auto_approve_single) ON courts FROM anon"
    )

    # Re-grant to authenticated; row-level policies from 0002 then narrow
    # access to ``owner_id = auth.uid()``.
    op.execute(
        "GRANT SELECT (auto_approve_single) ON courts TO authenticated"
    )
    op.execute(
        "GRANT UPDATE (auto_approve_single) ON courts TO authenticated"
    )


def downgrade() -> None:
    """Restore default-broad access to the column."""
    op.execute(
        "REVOKE SELECT (auto_approve_single) ON courts FROM authenticated"
    )
    op.execute(
        "REVOKE UPDATE (auto_approve_single) ON courts FROM authenticated"
    )
    # Restore anon + PUBLIC defaults.
    op.execute(
        "GRANT SELECT (auto_approve_single) ON courts TO anon"
    )
    op.execute(
        "GRANT UPDATE (auto_approve_single) ON courts TO anon"
    )
    op.execute(
        "GRANT SELECT (auto_approve_single) ON courts TO PUBLIC"
    )
    op.execute(
        "GRANT UPDATE (auto_approve_single) ON courts TO PUBLIC"
    )
