"""Initial schema — submissions, scoring_results, submission_votes, webhook_outbox.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-13 00:00:00.000000 UTC

Hand-authored because no live PostgreSQL is available at migration-generation
time.  Autogenerate is used for subsequent migrations once a Compose DB
service is running:  alembic -c app/db/migrations/alembic.ini revision --autogenerate

NOTE: CHECK constraints are added explicitly here because SQLAlchemy's
autogenerate does NOT emit CheckConstraint DDL automatically.
See docs/architecture/05_database_design.md §4.2 for the full migration plan.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── submissions ───────────────────────────────────────────────────────────
    op.create_table(
        "submissions",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.String(length=100), nullable=False),
        sa.Column("submission_type", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'superseded')",
            name="ck_submissions_status",
        ),
        sa.PrimaryKeyConstraint("submission_id", name="pk_submissions"),
    )
    # Single-column indexes
    op.create_index("ix_submissions_project_id", "submissions", ["project_id"])
    op.create_index("ix_submissions_user_id", "submissions", ["user_id"])
    op.create_index("ix_submissions_status", "submissions", ["status"])
    # Composite index for task-list query: pending rows per project
    op.create_index(
        "ix_submissions_project_status",
        "submissions",
        ["project_id", "status"],
    )
    # Self-referential FK added with ALTER TABLE (use_alter=True in ORM model)
    op.create_foreign_key(
        "fk_submissions_superseded_by",
        "submissions",
        "submissions",
        ["superseded_by"],
        ["submission_id"],
    )

    # ── scoring_results ───────────────────────────────────────────────────────
    op.create_table(
        "scoring_results",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        # Computed output — stored here, NOT in submissions (immutability / re-scoring)
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 100",
            name="ck_scoring_results_confidence_score",
        ),
        sa.Column(
            "breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "required_validations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "thresholds",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.submission_id"],
            name="fk_scoring_results_submission_id_submissions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scoring_results"),
        sa.UniqueConstraint("submission_id", name="uq_scoring_results_submission_id"),
    )
    op.create_index("ix_scoring_results_submission_id", "scoring_results", ["submission_id"])
    # Range queries for dashboards / leaderboards: WHERE confidence_score >= X
    op.create_index("ix_scoring_results_confidence_score", "scoring_results", ["confidence_score"])

    # ── submission_votes ──────────────────────────────────────────────────────
    op.create_table(
        "submission_votes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("vote", sa.String(length=10), nullable=False),
        sa.Column("user_trust_level", sa.Integer(), nullable=False),
        sa.Column("user_role", sa.String(length=50), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.submission_id"],
            name="fk_submission_votes_submission_id_submissions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_submission_votes"),
        sa.UniqueConstraint(
            "submission_id",
            "user_id",
            name="uq_submission_votes_submission_id_user_id",
        ),
    )
    op.create_index("ix_submission_votes_submission_id", "submission_votes", ["submission_id"])

    # ── webhook_outbox ────────────────────────────────────────────────────────
    op.create_table(
        "webhook_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.submission_id"],
            name="fk_webhook_outbox_submission_id_submissions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_webhook_outbox"),
    )
    op.create_index("ix_webhook_outbox_submission_id", "webhook_outbox", ["submission_id"])
    # Composite index used by the outbox poller query
    op.create_index(
        "ix_webhook_outbox_status_retry",
        "webhook_outbox",
        ["status", "next_retry_at"],
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("webhook_outbox")
    op.drop_table("submission_votes")
    op.drop_table("scoring_results")
    # Drop self-referential FK before dropping the table itself
    op.drop_constraint(
        "fk_submissions_superseded_by",
        "submissions",
        type_="foreignkey",
    )
    op.drop_table("submissions")
