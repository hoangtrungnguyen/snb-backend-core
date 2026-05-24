"""RLS policies for the skill_ratings table.

Revision ID: 0007
Revises: 0001
Create Date: 2026-05-23

Covers task grava-ea77.2.7:

    `skill_ratings`: INSERT/UPDATE only when `rated_by` is a court owner of a
                     court that the target player (player_id) has visited.

Policy summary (Supabase/PostgreSQL RLS):
  - Enable RLS on skill_ratings.
  - SELECT policy `skill_ratings_select_authenticated`:
      any authenticated user may read skill ratings — open read access.
  - INSERT policy `skill_ratings_insert_court_owner_visited`:
      authenticated users may insert a skill rating only when all conditions
      hold for the new row (WITH CHECK):
        1. rated_by = auth.uid() — the row's rated_by column must equal the
           caller's identity, preventing impersonation of other raters.
        2. auth.uid() is a court owner:
               auth.uid() IN (SELECT owner_id FROM courts)
        3. the player being rated (player_id) has a confirmed/completed booking
           at one of that court owner's courts:
               player_id IN (
                   SELECT user_id FROM bookings
                   WHERE court_id IN (
                       SELECT id FROM courts WHERE owner_id = auth.uid()
                   )
                   AND status IN ('confirmed', 'completed')
               )
  - UPDATE policy `skill_ratings_update_court_owner_visited`:
      same constraint as INSERT — both USING (row-access check) and WITH CHECK
      (new-value check) enforce the court-owner + visited-player relationship.
      rated_by = auth.uid() is included in both clauses to prevent a court
      owner from updating ratings they did not create or reassigning rated_by to
      another user. Only confirmed/completed bookings satisfy the "visited"
      constraint.

Non-qualifying users cannot insert or update skill_ratings — the default-deny
behaviour of RLS blocks any operation for which no matching policy exists once
RLS is enabled.

Service-role (background jobs / admin) bypasses RLS by default in Supabase, so
no explicit policy is required for it — see grava-ea77.2.10.

The downgrade() reverses all statements in inverse order.

Note: this migration uses down_revision = "0001" because the sibling RLS
migrations (0002–0006) may be merged in a different order. The Alembic branch
heads can be resolved into a linear chain when the PRs are integrated.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# Revision identifiers used by Alembic.
revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Enable RLS on skill_ratings and create access-control policies."""
    # Enable RLS — once enabled, default-deny applies for all roles until a
    # policy explicitly permits the operation.
    op.execute("ALTER TABLE skill_ratings ENABLE ROW LEVEL SECURITY")

    # SELECT: any authenticated user may read all skill ratings.
    # Ratings are considered semi-public information — the system surfaces them
    # in player profiles and match-making, so no row-level restriction applies
    # for reads beyond requiring an active session.
    op.execute(
        """
        CREATE POLICY skill_ratings_select_authenticated
        ON skill_ratings
        FOR SELECT
        TO authenticated
        USING (true)
        """
    )

    # INSERT: a court owner may rate a player only if that player has actually
    # visited one of the rater's courts (verified via the bookings table).
    #
    # The WITH CHECK clause enforces three conditions on the new row:
    #   1. rated_by = auth.uid() — the row's rated_by column must equal the
    #      caller's identity, preventing impersonation of other raters.
    #   2. auth.uid() is a court owner in the courts table.
    #   3. player_id has a confirmed/completed booking (visit) at a court owned
    #      by auth.uid().  Only confirmed or completed bookings count — cancelled
    #      or pending bookings do not satisfy the "visited" constraint.
    #
    # Because this is an INSERT policy, only WITH CHECK is used — there is no
    # existing row to check with USING.
    op.execute(
        """
        CREATE POLICY skill_ratings_insert_court_owner_visited
        ON skill_ratings
        FOR INSERT
        TO authenticated
        WITH CHECK (
            rated_by = auth.uid()
            AND auth.uid() IN (
                SELECT owner_id FROM courts
            )
            AND player_id IN (
                SELECT user_id FROM bookings
                WHERE court_id IN (
                    SELECT id FROM courts WHERE owner_id = auth.uid()
                )
                AND status IN ('confirmed', 'completed')
            )
        )
        """
    )

    # UPDATE: same constraint as INSERT — both USING (restricts which existing
    # rows can be targeted) and WITH CHECK (validates the updated row) enforce
    # the court-owner + visited-player relationship.
    #
    # rated_by = auth.uid() is added to both USING and WITH CHECK to prevent:
    #   - a court owner from updating ratings they did not create (USING).
    #   - a court owner from reassigning rated_by to another user (WITH CHECK).
    #
    # Only confirmed/completed bookings satisfy the "visited" constraint.
    op.execute(
        """
        CREATE POLICY skill_ratings_update_court_owner_visited
        ON skill_ratings
        FOR UPDATE
        TO authenticated
        USING (
            rated_by = auth.uid()
            AND auth.uid() IN (
                SELECT owner_id FROM courts
            )
            AND player_id IN (
                SELECT user_id FROM bookings
                WHERE court_id IN (
                    SELECT id FROM courts WHERE owner_id = auth.uid()
                )
                AND status IN ('confirmed', 'completed')
            )
        )
        WITH CHECK (
            rated_by = auth.uid()
            AND auth.uid() IN (
                SELECT owner_id FROM courts
            )
            AND player_id IN (
                SELECT user_id FROM bookings
                WHERE court_id IN (
                    SELECT id FROM courts WHERE owner_id = auth.uid()
                )
                AND status IN ('confirmed', 'completed')
            )
        )
        """
    )


def downgrade() -> None:
    """Drop skill_ratings RLS policies and disable RLS."""
    op.execute(
        "DROP POLICY IF EXISTS skill_ratings_update_court_owner_visited ON skill_ratings"
    )
    op.execute(
        "DROP POLICY IF EXISTS skill_ratings_insert_court_owner_visited ON skill_ratings"
    )
    op.execute(
        "DROP POLICY IF EXISTS skill_ratings_select_authenticated ON skill_ratings"
    )
    op.execute("ALTER TABLE skill_ratings DISABLE ROW LEVEL SECURITY")
