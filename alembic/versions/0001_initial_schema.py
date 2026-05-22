"""Initial schema — all core tables, FK constraints, indexes and updated_at triggers.

Revision ID: 0001
Revises: (none)
Create Date: 2026-05-22

Covers tasks:
  grava-ea77.1.1 — Alembic manages all migrations; alembic upgrade head idempotent
  grava-ea77.1.2 — Foreign keys enforced; cascades defined
  grava-ea77.1.3 — Required indexes
  grava-ea77.1.4 — updated_at trigger on bookings, slots, courts, booking_series
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Revision identifiers used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extension: pgcrypto for gen_random_uuid() (idempotent)
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # Helper function: set_updated_at
    # Used by updated_at triggers on bookings, slots, courts, booking_series
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$
        """
    )

    # ------------------------------------------------------------------
    # Table: users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            server_default="player",
            comment="owner | player",
        ),
        sa.Column(
            "fcm_tokens",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("last_lat", sa.Numeric(), nullable=True),
        sa.Column("last_lng", sa.Numeric(), nullable=True),
        sa.Column("location_updated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # Table: courts
    # ------------------------------------------------------------------
    op.create_table(
        "courts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column(
            "sport_types",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("price_per_hour", sa.Numeric(), nullable=True),
        sa.Column("operating_hours", postgresql.JSONB(), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("lat", sa.Numeric(), nullable=True),
        sa.Column("lng", sa.Numeric(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
            comment="pending | approved | suspended",
        ),
        sa.Column(
            "amenities",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "photos",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "auto_approve_single",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Unique index on courts.slug (grava-ea77.1.3)
    op.create_index("uq_courts_slug", "courts", ["slug"], unique=True)

    # Index on courts.owner_id (grava-ea77.1.3)
    op.create_index("ix_courts_owner_id", "courts", ["owner_id"])

    # Index on courts(lat, lng) (grava-ea77.1.3)
    op.create_index("ix_courts_lat_lng", "courts", ["lat", "lng"])

    # updated_at trigger on courts (grava-ea77.1.4)
    op.execute(
        """
        CREATE TRIGGER trg_courts_updated_at
        BEFORE UPDATE ON courts
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )

    # ------------------------------------------------------------------
    # Table: recurrence_rules
    # ------------------------------------------------------------------
    op.create_table(
        "recurrence_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "court_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("days_of_week", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # Table: slots
    # ------------------------------------------------------------------
    op.create_table(
        "slots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "court_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("start_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="open",
            comment="open | booked | blocked | maintenance",
        ),
        sa.Column(
            "access_policy",
            sa.Text(),
            nullable=False,
            server_default="open",
            comment="private | open",
        ),
        sa.Column("max_players", sa.Integer(), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("recurrence_rule_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Index on slots(court_id, start_at, status) (grava-ea77.1.3)
    op.create_index(
        "ix_slots_court_id_start_at_status",
        "slots",
        ["court_id", "start_at", "status"],
    )

    # updated_at trigger on slots (grava-ea77.1.4)
    op.execute(
        """
        CREATE TRIGGER trg_slots_updated_at
        BEFORE UPDATE ON slots
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )

    # ------------------------------------------------------------------
    # Table: booking_series
    # ------------------------------------------------------------------
    op.create_table(
        "booking_series",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "court_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pattern",
            sa.Text(),
            nullable=False,
            comment="daily | weekly | biweekly | custom_days",
        ),
        sa.Column("days_of_week", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
            comment="pending | active | cancelled | completed",
        ),
        sa.Column(
            "access_policy",
            sa.Text(),
            nullable=False,
            server_default="private",
            comment="private | open",
        ),
        sa.Column("max_players", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("total_price", sa.Numeric(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # updated_at trigger on booking_series (grava-ea77.1.4)
    op.execute(
        """
        CREATE TRIGGER trg_booking_series_updated_at
        BEFORE UPDATE ON booking_series
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )

    # ------------------------------------------------------------------
    # Table: bookings
    # ------------------------------------------------------------------
    op.create_table(
        "bookings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("slots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "court_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("courts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # booking_series_id: SET NULL when booking_series row is deleted (grava-ea77.1.2)
        sa.Column(
            "booking_series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("booking_series.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("customer_name", sa.Text(), nullable=True),
        sa.Column("customer_phone", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
            comment="pending | confirmed | cancelled | completed",
        ),
        sa.Column("price_per_hour", sa.Numeric(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("total_price", sa.Numeric(), nullable=True),
        sa.Column("is_owner_slot", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_walk_in", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "is_auto_approved", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "reminder_sent", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Index on bookings(slot_id, status) (grava-ea77.1.3)
    op.create_index("ix_bookings_slot_id_status", "bookings", ["slot_id", "status"])

    # Index on bookings(user_id) (grava-ea77.1.3)
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])

    # Index on bookings(booking_series_id) (grava-ea77.1.3)
    op.create_index(
        "ix_bookings_booking_series_id", "bookings", ["booking_series_id"]
    )

    # updated_at trigger on bookings (grava-ea77.1.4)
    op.execute(
        """
        CREATE TRIGGER trg_bookings_updated_at
        BEFORE UPDATE ON bookings
        FOR EACH ROW EXECUTE FUNCTION set_updated_at()
        """
    )

    # ------------------------------------------------------------------
    # Table: slot_participants
    # ------------------------------------------------------------------
    op.create_table(
        "slot_participants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("slots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "payment_status",
            sa.Text(),
            nullable=False,
            server_default="unpaid",
            comment="paid | unpaid | partial",
        ),
        sa.Column(
            "payment_method",
            sa.Text(),
            nullable=True,
            comment="cash | transfer | app_wallet",
        ),
    )

    # ------------------------------------------------------------------
    # Table: slot_join_requests
    # ------------------------------------------------------------------
    op.create_table(
        "slot_join_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("slots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
            comment="pending | approved | rejected",
        ),
        sa.Column(
            "requested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ------------------------------------------------------------------
    # Table: notifications
    # ------------------------------------------------------------------
    op.create_table(
        "notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "related_booking_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "related_slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("slots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "related_series_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("booking_series.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Index on notifications(user_id, read) (grava-ea77.1.3)
    op.create_index(
        "ix_notifications_user_id_read", "notifications", ["user_id", "read"]
    )

    # ------------------------------------------------------------------
    # Table: slot_push_log
    # ------------------------------------------------------------------
    op.create_table(
        "slot_push_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("slots.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pushed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Index on slot_push_log(user_id, pushed_at) (grava-ea77.1.3)
    op.create_index(
        "ix_slot_push_log_user_id_pushed_at", "slot_push_log", ["user_id", "pushed_at"]
    )

    # ------------------------------------------------------------------
    # Table: skill_ratings
    # ------------------------------------------------------------------
    op.create_table(
        "skill_ratings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "player_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sport", sa.Text(), nullable=False),
        sa.Column(
            "level",
            sa.Text(),
            nullable=False,
            comment="beginner | intermediate | advanced | professional",
        ),
        sa.Column(
            "rated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Drop tables in reverse dependency order.
    op.drop_table("skill_ratings")
    op.drop_table("slot_push_log")
    op.drop_table("notifications")
    op.drop_table("slot_join_requests")
    op.drop_table("slot_participants")
    op.drop_table("bookings")
    op.drop_table("booking_series")
    op.drop_table("slots")
    op.drop_table("recurrence_rules")
    op.drop_table("courts")
    op.drop_table("users")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at() CASCADE")
