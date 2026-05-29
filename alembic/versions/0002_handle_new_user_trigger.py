"""handle_new_user trigger — auto-create public.customers row on first Supabase signup.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-24

Covers task:
  grava-1132.2.10 — On first successful login: auto-creates `users` row
                    with `role = player` (via `handle_new_user` trigger)

When a new row is inserted into auth.users (Supabase auth schema), this
trigger fires and inserts a corresponding row into public.customers with
role = 'player'.  ON CONFLICT DO NOTHING ensures idempotency in case the
public.customers row already exists.
"""

from __future__ import annotations

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Function: handle_new_user
    # Inserts a public.customers row when auth.users gets a new signup.
    # ON CONFLICT DO NOTHING keeps the operation idempotent.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.handle_new_user()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        SECURITY DEFINER
        AS $$
        BEGIN
            INSERT INTO public.customers (id, role)
            VALUES (NEW.id, 'player')
            ON CONFLICT DO NOTHING;
            RETURN NEW;
        END;
        $$
        """
    )

    # ------------------------------------------------------------------
    # Trigger: handle_new_user
    # Fires AFTER INSERT on auth.users FOR EACH ROW.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TRIGGER handle_new_user
        AFTER INSERT ON auth.users
        FOR EACH ROW EXECUTE FUNCTION public.handle_new_user()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS handle_new_user ON auth.users"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS public.handle_new_user()"
    )
