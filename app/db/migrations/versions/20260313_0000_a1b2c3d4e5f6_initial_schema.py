"""Initial schema — Sprint 5 Lifecycle Ledger architecture.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-13 00:00:00.000000 UTC

Hand-authored because no live PostgreSQL is available at migration-generation
time.  Autogenerate is used for subsequent migrations once a Compose DB
service is running.

Sprint 5 changes (in-place rewrite):
* submissions: removed user_context (moved to submission_user_snapshots).
* scoring_results: DROPPED — replaced by scoring_snapshots + status_ledger.
* submission_user_snapshots: NEW — 1:1 snapshot of user state at submission time.
* scoring_snapshots: NEW — immutable technical analysis (1:N per submission).
* status_ledger: NEW — append-only lifecycle log with backward-chaining via
  'supersedes' self-referencing FK.
* submission_votes: removed UNIQUE(submission_id, user_id) — fully append-only;
  updated CHECK constraint for vote values; added updated_at.
* webhook_outbox: added 'dead' to status check constraint.

All string fields use sa.Text (PostgreSQL TEXT) instead of VARCHAR(n).
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
    # Strictly write-once audit log: no user_context, no status.
    # User snapshot lives in submission_user_snapshots.
    # All lifecycle state lives in status_ledger.
    op.create_table(
        "submissions",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("measurement_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("submission_id", name="pk_submissions"),
    )
    op.create_index("ix_submissions_project_id", "submissions", ["project_id"])
    op.create_index("ix_submissions_user_id", "submissions", ["user_id"])
    op.create_index(
        "ix_submissions_triplet",
        "submissions",
        ["project_id", "entity_id", "measurement_id"],
    )
    op.create_index(
        "ix_submissions_project_user",
        "submissions",
        ["project_id", "user_id"],
    )

    # ── submission_user_snapshots ─────────────────────────────────────────────
    # 1:1 with submissions. Captures user state at submission time.
    # Written once — never updated.
    op.create_table(
        "submission_user_snapshots",
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("trust_level", sa.Integer(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("total_submissions", sa.Integer(), nullable=False),
        sa.Column("user_account_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_account_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "custom_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
            name="fk_submission_user_snapshots_submission_id_submissions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("submission_id", name="pk_submission_user_snapshots"),
    )
    op.create_index(
        "ix_submission_user_snapshots_user_id",
        "submission_user_snapshots",
        ["user_id"],
    )

    # ── scoring_snapshots ─────────────────────────────────────────────────────
    # Static technical analysis — written once per scoring event (1:N per
    # submission to support re-scoring in Case G).
    op.create_table(
        "scoring_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 100",
            name="ck_scoring_snapshots_confidence_score",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["submission_id"],
            ["submissions.submission_id"],
            name="fk_scoring_snapshots_submission_id_submissions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_scoring_snapshots"),
        # NOTE: no UNIQUE on submission_id — 1:N intentional for re-scoring
    )
    op.create_index(
        "ix_scoring_snapshots_submission_id",
        "scoring_snapshots",
        ["submission_id"],
    )
    op.create_index(
        "ix_scoring_snapshots_confidence_score",
        "scoring_snapshots",
        ["confidence_score"],
    )
    op.create_index(
        "ix_scoring_snapshots_submission_created",
        "scoring_snapshots",
        ["submission_id", "created_at"],
    )

    # ── status_ledger ─────────────────────────────────────────────────────────
    # Append-only lifecycle log. Every state transition is a new INSERT.
    # Backward-chaining: 'supersedes' points to the entry this row replaces.
    # Old entries are NEVER modified.
    # The 'supersedes' self-referencing FK is deferred (use_alter=True).
    op.create_table(
        "status_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("scoring_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending_review",
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'voided')",
            name="ck_status_ledger_status",
        ),
        sa.Column("trust_delta", sa.Integer(), nullable=False, server_default="0"),
        # Backward pointer — created via use_alter below
        sa.Column("supersedes", sa.Uuid(), nullable=True),
        sa.Column("supersede_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "supersede_reason IS NULL OR supersede_reason IN ("
            "'edited', 'deleted', 'voting_concluded', "
            "'auto_approve', 'auto_reject', 'admin_overwrite', 're-scored')",
            name="ck_status_ledger_supersede_reason",
        ),
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
            name="fk_status_ledger_submission_id_submissions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scoring_snapshot_id"],
            ["scoring_snapshots.id"],
            name="fk_status_ledger_scoring_snapshot_id_scoring_snapshots",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_status_ledger"),
    )
    op.create_index("ix_status_ledger_submission_id", "status_ledger", ["submission_id"])
    op.create_index("ix_status_ledger_status", "status_ledger", ["status"])
    op.create_index(
        "ix_status_ledger_submission_created",
        "status_ledger",
        ["submission_id", "created_at"],
    )
    # Deferred self-referencing FK for the backward-chaining pointer
    op.create_foreign_key(
        "fk_status_ledger_supersedes_self",
        "status_ledger",
        "status_ledger",
        ["supersedes"],
        ["id"],
    )

    # ── submission_votes ──────────────────────────────────────────────────────
    # Fully append-only — NO unique constraint on (submission_id, user_id).
    # When a user changes their mind they insert a new row; the VotingService
    # resolves the "active" vote as the latest row per user_id per submission.
    op.create_table(
        "submission_votes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("vote", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(vote IN ('approve', 'reject') OR (is_override = true AND vote = 'voided'))",
            name="ck_submission_votes_vote",
        ),
        sa.Column("is_override", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("user_trust_level", sa.Integer(), nullable=False),
        sa.Column("user_role", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
            name="fk_submission_votes_submission_id_submissions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_submission_votes"),
    )
    op.create_index(
        "ix_submission_votes_submission_id",
        "submission_votes",
        ["submission_id"],
    )
    op.create_index(
        "ix_submission_votes_submission_user",
        "submission_votes",
        ["submission_id", "user_id"],
    )
    # Index for latest-vote-per-user lookups
    op.create_index(
        "ix_submission_votes_submission_user_created",
        "submission_votes",
        ["submission_id", "user_id", "created_at"],
    )

    # ── webhook_outbox ────────────────────────────────────────────────────────
    op.create_table(
        "webhook_outbox",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="pending",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'delivered', 'failed', 'dead')",
            name="ck_webhook_outbox_status",
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
    op.create_index(
        "ix_webhook_outbox_submission_id",
        "webhook_outbox",
        ["submission_id"],
    )
    op.create_index(
        "ix_webhook_outbox_status_retry",
        "webhook_outbox",
        ["status", "next_retry_at"],
    )


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("webhook_outbox")
    op.drop_table("submission_votes")
    # Drop the deferred self-ref FK before dropping status_ledger
    op.drop_constraint(
        "fk_status_ledger_supersedes_self",
        "status_ledger",
        type_="foreignkey",
    )
    op.drop_table("status_ledger")
    op.drop_table("scoring_snapshots")
    op.drop_table("submission_user_snapshots")
    op.drop_table("submissions")
