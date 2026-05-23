"""Enable Row Level Security on all application tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24

Covers task:
  grava-ea77.2.10 — RLS enabled on all tables; service-role key bypasses RLS
                    for background jobs only.

Notes:
  - ALTER TABLE ... ENABLE ROW LEVEL SECURITY activates RLS enforcement for
    the anon and authenticated roles on every table.
  - The Supabase service-role key automatically bypasses RLS at the connection
    level (SET ROLE ... / SET LOCAL role = service_role). No extra policy is
    needed for background jobs that use the service-role key.
  - By default, once RLS is enabled and no permissive policy exists, all access
    is denied for anon/authenticated unless a specific policy grants it. Those
    fine-grained policies are handled in subsequent migration tasks.
"""

from __future__ import annotations

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None

# All application tables that require RLS.
_TABLES = [
    "users",
    "courts",
    "recurrence_rules",
    "slots",
    "booking_series",
    "bookings",
    "slot_participants",
    "slot_join_requests",
    "notifications",
    "slot_push_log",
    "skill_ratings",
]


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
