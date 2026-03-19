"""
SubmissionVote ORM model.

Sprint 5 (Lifecycle Ledger) changes
-------------------------------------
* Fully **append-only** — the UNIQUE(submission_id, user_id) constraint has
  been removed.  When a reviewer changes their vote they simply insert a new
  row.  The VotingService resolves the "active" vote for each user as the
  row with the highest ``created_at`` for that (submission_id, user_id) pair.
* Updated CHECK constraint on ``vote``:
  - Normal votes: ``'approve'`` or ``'reject'``.
  - Override votes (``is_override=True``): additionally allows ``'voided'``.
* Added ``updated_at`` via ``TimestampMixin`` for full consistency.

Append-Only Voting rationale
-----------------------------
Treating votes as an immutable log (like the status_ledger) gives a complete
audit trail of every mind-change made by every reviewer.  The "active" vote
per reviewer per submission is always the most recent row — the same
latest-row-wins pattern used everywhere in the Lifecycle Ledger architecture.

References
----------
* Database design: docs/architecture/05_database_design.md §1.5
* Rule 10:         .junie/AGENTS.md — Immutable Submissions & Append-Only State
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.submission import Submission


class SubmissionVote(Base, TimestampMixin):
    """
    Single vote row in the append-only voting log.

    Multiple rows may exist per (submission_id, user_id) pair.  The active
    vote is the row with the highest ``created_at``.

    ``is_override=True`` marks an Admin Override vote, which:
    * Bypasses normal eligibility and duplicate checks.
    * Additionally allows ``vote='voided'`` (enforced by CHECK constraint).
    * Forces the submission into a terminal state immediately.

    ``user_trust_level`` and ``user_role`` are snapshotted at cast time so
    that historical eligibility decisions remain auditable even if the user's
    profile changes.
    """

    __tablename__ = "submission_votes"

    __table_args__ = (
        CheckConstraint(
            "(vote IN ('approve', 'reject') OR (is_override = true AND vote = 'voided'))",
            name="ck_submission_votes_vote",
        ),
        # Composite index for latest-vote-per-user lookups
        Index(
            "ix_submission_votes_submission_user_created",
            "submission_id",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    # 'approve' | 'reject' for normal votes; additionally 'voided' for overrides
    vote: Mapped[str] = mapped_column(nullable=False)
    # True when this vote is an Admin Override (forces terminal state)
    is_override: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    # Snapshotted at cast time for historical audit
    user_trust_level: Mapped[int] = mapped_column(Integer(), nullable=False)
    user_role: Mapped[str] = mapped_column(nullable=False)
    note: Mapped[str | None] = mapped_column(nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    submission: Mapped[Submission] = relationship(
        "Submission",
        back_populates="votes",
    )
