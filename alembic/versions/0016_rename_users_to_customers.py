"""Rename public.users to public.customers.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-26

Renames the users table to customers to avoid confusion with Django's
built-in auth_user table.
"""

from __future__ import annotations

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_tables
                WHERE schemaname = 'public' AND tablename = 'users'
            ) THEN
                ALTER TABLE public.users RENAME TO customers;
            END IF;
        END;
        $$
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.customers RENAME TO users")
