"""
SubmissionUserSnapshot ORM model.

Captures the submitting user's identity and stats at the exact moment of
submission as a 1:1 extension of the ``submissions`` table.  This table is
strictly write-once — it is NEVER updated after creation.

Normalising user data out of a JSONB blob into flat indexed columns enables:
* Efficient queries on ``user_id``, ``role``, and ``trust_level``.
* A reliable audit trail of the user's standing at decision time.
* ``total_submissions`` — the count of active Winnow chains for this user
  at the time of this submission (calculated by the scoring service).

References
----------
* Database design: docs/architecture/05_database_design.md §1.2
* Rule 10:         .junie/AGENTS.md — Immutable Submissions & Append-Only State
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.submission import Submission


class SubmissionUserSnapshot(Base, TimestampMixin):
    """
    Point-in-time snapshot of the submitting user's identity and stats.

    The ``submission_id`` is both the PK and a FK to ``submissions``,
    enforcing the strict 1:1 relationship at the database level.

    ``total_submissions`` is calculated by the scoring service at creation time
    (count of active chains for this ``user_id`` excluding voided/edited/deleted
    chains).  It is NOT recalculated on reads — it is an audit snapshot.

    ``custom_data`` holds any project-specific user metadata forwarded by the
    client system that does not fit the flat columns.
    """

    __tablename__ = "submission_user_snapshots"

    # PK is also the FK — enforces 1:1 at the DB level
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    role: Mapped[str] = mapped_column(nullable=False)
    trust_level: Mapped[int] = mapped_column(Integer(), nullable=False)
    username: Mapped[str | None] = mapped_column(nullable=True)
    # Winnow-calculated count of active chains at submission time
    total_submissions: Mapped[int] = mapped_column(Integer(), nullable=False)
    user_account_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    user_account_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Project-specific user metadata (flexible extension point)
    custom_data: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    submission: Mapped[Submission] = relationship(
        "Submission",
        back_populates="user_snapshot",
    )
