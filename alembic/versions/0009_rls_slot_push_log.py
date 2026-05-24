"""RLS policies for the slot_push_log table.

Revision ID: 0009
Revises: 0001
Create Date: 2026-05-24

Covers task grava-ea77.2.9:

    `slot_push_log`: SELECT/INSERT only service-role.

Policy summary (Supabase/PostgreSQL RLS):
  - Enable RLS on slot_push_log.
  - No SELECT, INSERT, UPDATE or DELETE policies are granted to the `anon`
    or `authenticated` roles.

Rationale: slot_push_log is an internal audit/deduplication log written and
read exclusively by background jobs (slot-push worker). These jobs connect
using the Supabase service-role key. The service-role bypasses RLS by default
in Supabase — it is exempt from all row-level policies. Therefore, enabling
RLS without any explicit policy is equivalent to granting full access to the
service-role while blocking every other role by the default-deny posture.

Regular authenticated users (mobile app, dashboard) must never be able to
directly SELECT or INSERT rows in this table; those operations are an
implementation detail of the push-notification subsystem.

Service-role (background jobs / admin) bypasses RLS by default in Supabase, so
no explicit policy is required for it — see grava-ea77.2.10.

Note: this migration uses down_revision = "0001" because the sibling RLS
migrations (0002–0010) may be merged in a different order. The Alembic branch
heads can be resolved into a linear chain when the PRs are integrated.

The downgrade reverses the single statement in inverse order.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on slot_push_log.

    Enabling RLS without any permissive policies creates a default-deny
    posture: all operations from the `anon` and `authenticated` roles are
    blocked.  The service-role key bypasses RLS automatically (Supabase
    default), so background push workers retain full read/write access.
    """
    op.execute("ALTER TABLE slot_push_log ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Disable RLS on slot_push_log, restoring open access for all roles."""
    op.execute("ALTER TABLE slot_push_log DISABLE ROW LEVEL SECURITY")
