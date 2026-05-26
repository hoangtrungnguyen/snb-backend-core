"""Create slot_push_log table for last-minute push deduplication.

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-26

Covers task grava-52bc.4.5:

    Rate limit: `slot_push_log` table tracks 1 push per user per slot.

Schema:
    slot_push_log (
        id         uuid         PRIMARY KEY DEFAULT gen_random_uuid(),
        slot_id    text         NOT NULL,
        user_id    uuid         NOT NULL,
        pushed_at  timestamptz  NOT NULL DEFAULT now()
    )

Unique constraint on (slot_id, user_id) prevents duplicate push entries.

The downgrade drops the table entirely.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Revision identifiers used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create slot_push_log table with deduplication constraint."""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS slot_push_log (
            id        uuid        NOT NULL DEFAULT gen_random_uuid(),
            slot_id   text        NOT NULL,
            user_id   uuid        NOT NULL,
            pushed_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT slot_push_log_pkey PRIMARY KEY (id),
            CONSTRAINT slot_push_log_slot_user_unique UNIQUE (slot_id, user_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_slot_push_log_slot_id
        ON slot_push_log (slot_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_slot_push_log_user_id
        ON slot_push_log (user_id)
        """
    )


def downgrade() -> None:
    """Drop slot_push_log table."""
    op.execute("DROP TABLE IF EXISTS slot_push_log")
