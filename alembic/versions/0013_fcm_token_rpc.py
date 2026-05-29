"""Supabase RPC helpers for FCM token management (grava-52bc.1).

Revision ID: 0013
Revises: 0001
Create Date: 2026-05-26

Covers grava-52bc.1 (BCORE-050) — FCM device token registration.

Two lightweight PostgreSQL functions exposed via Supabase RPC:

  register_fcm_token(p_user_id uuid, p_token text)
      Appends p_token to users.fcm_tokens[] if not already present
      (idempotent via array_append + a NOT = ANY check).
      Executed with SECURITY DEFINER so the authenticated role can
      update the row without needing a direct UPDATE policy on users.

  deregister_fcm_token(p_user_id uuid, p_token text)
      Removes p_token from users.fcm_tokens[] using array_remove.
      Also SECURITY DEFINER.

Both functions are owned by the postgres role (SECURITY DEFINER owner)
and granted EXECUTE to the authenticated role so Supabase can call them
from a client-authenticated JWT context.

Why SECURITY DEFINER instead of a direct REST PATCH?
   The users table has RLS enabled (migration 0002) and the authenticated
   role may not hold an UPDATE policy broad enough to mutate fcm_tokens.
   Using SECURITY DEFINER functions lets us keep the RLS surface minimal
   while still exposing this controlled write path via PostgREST RPC.

Downgrade removes both functions.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # register_fcm_token — append token if not already present
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION register_fcm_token(
            p_user_id  uuid,
            p_token    text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            UPDATE customers
            SET fcm_tokens = array_append(fcm_tokens, p_token)
            WHERE id = p_user_id
              AND NOT (p_token = ANY(fcm_tokens));
        END;
        $$
        """
    )

    op.execute(
        "GRANT EXECUTE ON FUNCTION register_fcm_token(uuid, text) TO authenticated"
    )

    # ------------------------------------------------------------------
    # deregister_fcm_token — remove all occurrences of token
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION deregister_fcm_token(
            p_user_id  uuid,
            p_token    text
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
            UPDATE customers
            SET fcm_tokens = array_remove(fcm_tokens, p_token)
            WHERE id = p_user_id;
        END;
        $$
        """
    )

    op.execute(
        "GRANT EXECUTE ON FUNCTION deregister_fcm_token(uuid, text) TO authenticated"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS deregister_fcm_token(uuid, text)")
    op.execute("DROP FUNCTION IF EXISTS register_fcm_token(uuid, text)")
