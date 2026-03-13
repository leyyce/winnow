"""
ScoringResult ORM model.

Stores the detailed scoring output for a submission in a 1:1 relationship
with the ``submissions`` table (ADR-DB-002).  Keeping scoring data in a
separate table prevents bloating the hot ``submissions`` row and allows the
scoring schema to evolve independently.

``breakdown``, ``required_validations``, and ``thresholds`` are stored as
JSONB so that the internal structure of each project's scoring rules can
change without a schema migration.

References
----------
* Database design: docs/architecture/05_database_design.md §1.2, §6 step 3
* ADR-DB-002:      Separate scoring_results table
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.submission import Submission


class ScoringResult(Base):
    """
    Detailed scoring output linked 1:1 to a ``Submission``.

    ``breakdown`` holds the per-rule score list returned by the pipeline.
    ``required_validations`` holds the governance tier snapshot (threshold_score,
    role_weights, required_min_trust) computed at submission time and used by
    the voting service to evaluate votes without re-running governance logic.
    ``thresholds`` holds the advisory routing thresholds from the registry.
    """

    __tablename__ = "scoring_results"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,   # enforces the 1:1 relationship at DB level
        index=True,
    )
    # Computed output — belongs here, NOT in submissions (immutability / re-scoring)
    confidence_score: Mapped[float] = mapped_column(nullable=False)
    # Per-rule breakdown: list[{rule, weight, score, weighted_score, details}]
    breakdown: Mapped[list] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    # Governance tier snapshot: {threshold_score, role_weights, required_min_trust}
    required_validations: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    # Advisory thresholds: {auto_approve, auto_reject}
    thresholds: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(__import__("datetime").timezone.utc),
        server_default=func.now(),
    )

    # ── Table-level constraints ─────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 100",
            name="ck_scoring_results_confidence_score",
        ),
    )
    # ── Relationship ──────────────────────────────────────────────────────────
    submission: Mapped[Submission] = relationship(
        "Submission",
        back_populates="scoring_result",
    )

    def __repr__(self) -> str:
        return f"<ScoringResult submission_id={self.submission_id!s}>"


__all__ = ["ScoringResult"]
