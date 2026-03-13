"""
SubmissionVote ORM model.

Records a single reviewer vote (approve/reject) cast on a pending submission.
The composite UNIQUE(submission_id, user_id) constraint enforces the
duplicate-vote prevention rule at the database level, acting as a final
safety net below the service-layer check.

References
----------
* Database design: docs/architecture/05_database_design.md §1.2, §6 step 4
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.submission import Submission


class SubmissionVote(Base):
    """
    A single reviewer vote on a submission.

    ``user_trust_level`` and ``user_role`` are snapshotted at vote time so
    that the weighted-score calculation remains deterministic even if the
    user's trust or role changes after the vote was cast.
    """

    __tablename__ = "submission_votes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    # "approve" or "reject"
    vote: Mapped[str] = mapped_column(String(10), nullable=False)

    # Snapshotted reviewer credentials at vote time
    user_trust_level: Mapped[int] = mapped_column(nullable=False)
    user_role: Mapped[str] = mapped_column(String(50), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(__import__("datetime").timezone.utc),
        server_default=func.now(),
    )

    # ── Table-level constraints ───────────────────────────────────────────────
    __table_args__ = (
        # DB-level duplicate-vote guard (service layer checks first)
        UniqueConstraint("submission_id", "user_id", name="uq_submission_votes_submission_id_user_id"),
    )

    # ── Relationship ──────────────────────────────────────────────────────────
    submission: Mapped[Submission] = relationship(
        "Submission",
        back_populates="votes",
    )

    def __repr__(self) -> str:
        return (
            f"<SubmissionVote submission_id={self.submission_id!s} "
            f"user_id={self.user_id!s} vote={self.vote!r}>"
        )


__all__ = ["SubmissionVote"]
