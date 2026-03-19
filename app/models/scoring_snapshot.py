"""
ScoringSnapshot ORM model.

Stores the computed scoring output for a submission.  This is static,
immutable technical analysis — written once per scoring event and never
updated.

The relationship with ``submissions`` is **one-to-many** (not one-to-one):
multiple ``ScoringSnapshot`` rows may exist per submission to support Case G
(Re-Scoring), where a new scoring event creates a fresh snapshot without
modifying the original.

The active (latest) snapshot for a submission is always the row with the
highest ``created_at`` for that ``submission_id``.

The composite index ``(submission_id, created_at DESC)`` makes
"latest snapshot" lookups O(log N).

References
----------
* Database design: docs/architecture/05_database_design.md §1.3
* Rule 10:         .junie/AGENTS.md — Immutable Submissions & Append-Only State
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.status_ledger import StatusLedger
    from app.models.submission import Submission


class ScoringSnapshot(Base, TimestampMixin):
    """
    Immutable technical analysis output linked to a ``Submission``.

    Multiple rows may exist per submission (1:N) to support re-scoring.
    The active snapshot is the row with the latest ``created_at`` for a
    given ``submission_id``.

    ``breakdown`` holds the per-rule score list returned by the pipeline.
    ``required_validations`` holds the governance tier snapshot (role_configs,
    thresholds) used by the voting service to evaluate votes without
    re-running governance logic.
    ``thresholds`` holds the advisory routing thresholds from the registry.
    """

    __tablename__ = "scoring_snapshots"

    __table_args__ = (
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 100",
            name="ck_scoring_snapshots_confidence_score",
        ),
        Index(
            "ix_scoring_snapshots_submission_created",
            "submission_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        # NOTE: UNIQUE intentionally absent — 1:N supports re-scoring
    )
    confidence_score: Mapped[float] = mapped_column(Float(), nullable=False)
    # Per-rule breakdown: list[{rule, weight, score, weighted_score, details}]
    breakdown: Mapped[list] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    # Governance tier snapshot: role_configs, default_config, blocked_roles,
    # threshold_score, required_approvals
    required_validations: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    # Advisory routing thresholds: {auto_approve_min, manual_review_min}
    thresholds: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    submission: Mapped[Submission] = relationship(
        "Submission",
        back_populates="scoring_snapshots",
    )
    ledger_entries: Mapped[list[StatusLedger]] = relationship(
        "StatusLedger",
        back_populates="scoring_snapshot",
        cascade="all, delete-orphan",
    )
