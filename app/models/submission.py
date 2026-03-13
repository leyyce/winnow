"""
Submission ORM model.

Represents a single point-in-time measurement snapshot submitted by a citizen
scientist.  Submissions are immutable after creation (Rule 10).  Status
transitions are append-only: pending_review → approved | rejected | superseded.

The ``superseded_by`` self-referential FK records the UUID of the replacement
submission when a user corrects their data in the client system.

References
----------
* Database design: docs/architecture/05_database_design.md §1.2, §6 step 2
* Rule 10:         .junie/AGENTS.md — Immutable Submissions & Append-Only State
"""
from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.scoring_result import ScoringResult
    from app.models.submission_vote import SubmissionVote
    from app.models.webhook_outbox import WebhookOutbox


class SubmissionStatus(StrEnum):
    """Valid lifecycle states for a submission (mirrors CHECK constraint in DB)."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class Submission(Base, TimestampMixin):
    """
    Persisted record of a scored submission.

    The ``submission_id`` is supplied by the client (UUID generated in the
    client system) and acts as the natural primary key.  Winnow trusts the
    client to generate globally unique UUIDs (ADR-DB-003).

    ``user_context`` and ``raw_payload`` are stored as JSONB so that Winnow
    never needs to know the internal structure of client domain data
    (ADR-DB-001).  The scoring result details are stored in a separate
    ``scoring_results`` row (ADR-DB-002).
    """

    __tablename__ = "submissions"

    # Client-supplied UUID — used as the natural PK (ADR-DB-003)
    submission_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    project_id: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    # Submission variant, e.g. "tree_measurement" — from SubmissionMetadata.submission_type
    submission_type: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    # Full UserContext snapshot — JSONB on PostgreSQL, JSON on SQLite (tests)
    user_context: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    # Raw validated payload for audit trail
    raw_payload: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=SubmissionStatus.PENDING_REVIEW,
        index=True,
    )
    # Self-referential FK — set when this submission is superseded by another
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "submissions.submission_id",
            use_alter=True,
            name="fk_submissions_superseded_by",
        ),
        nullable=True,
        default=None,
    )

    # ── Table-level constraints and indexes ───────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'superseded')",
            name="ck_submissions_status",
        ),
        # Composite index for task-list queries: find pending submissions per project
        Index("ix_submissions_project_status", "project_id", "status"),
    )

    # ── Relationships — string refs resolved lazily by SQLAlchemy mapper ──────
    scoring_result: Mapped[ScoringResult | None] = relationship(
        "ScoringResult",
        back_populates="submission",
        uselist=False,
        cascade="all, delete-orphan",
    )
    votes: Mapped[list[SubmissionVote]] = relationship(
        "SubmissionVote",
        back_populates="submission",
        cascade="all, delete-orphan",
    )
    outbox_events: Mapped[list[WebhookOutbox]] = relationship(
        "WebhookOutbox",
        back_populates="submission",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Submission submission_id={self.submission_id!s} "
            f"project={self.project_id!r} status={self.status!r}>"
        )


__all__ = ["Submission", "SubmissionStatus"]
