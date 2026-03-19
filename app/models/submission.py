"""
Submission ORM model.

Represents a single point-in-time measurement snapshot submitted by a citizen
scientist.  Submissions are **strictly write-once audit-log entries** (Rule 10).
They must NEVER be updated after creation.

Sprint 5 (Lifecycle Ledger) changes
-------------------------------------
* ``user_context`` JSONB blob removed — user state is now stored in the
  1:1 ``submission_user_snapshots`` table via the ``user_snapshot`` relationship.
* ``scoring_results`` relationship removed — replaced by:
  - ``scoring_snapshots`` (one-to-many, immutable analysis)
  - ``ledger_entries``   (one-to-many, append-only state transitions)

The identity triplet ``(project_id, entity_id, measurement_id)`` uniquely
identifies a versioned measurement and drives the auto-supersede logic in the
scoring service.

References
----------
* Database design: docs/architecture/05_database_design.md §1.1
* Rule 10:         .junie/AGENTS.md — Immutable Submissions & Append-Only State
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.scoring_snapshot import ScoringSnapshot
    from app.models.status_ledger import StatusLedger
    from app.models.submission_user_snapshot import SubmissionUserSnapshot
    from app.models.submission_vote import SubmissionVote
    from app.models.webhook_outbox import WebhookOutbox


class Submission(Base, TimestampMixin):
    """
    Persisted, immutable record of a scored submission.

    The ``submission_id`` is supplied by the client (UUID generated in the
    client system) and acts as the natural primary key, enabling idempotent
    re-submissions (ADR-DB-003).

    ``raw_payload`` is stored as JSONB so Winnow remains decoupled from the
    internal structure of client domain data (ADR-DB-001).

    The identity triplet ``(project_id, entity_id, measurement_id)`` is indexed
    to support high-performance auto-supersede lookups.

    User state at submission time lives in the 1:1 ``user_snapshot``
    relationship (``submission_user_snapshots`` table).
    Lifecycle state lives in the one-to-many ``ledger_entries`` relationship
    (``status_ledger`` table).
    """

    __tablename__ = "submissions"

    __table_args__ = (
        # Composite index on the identity triplet — drives auto-supersede lookup
        Index(
            "ix_submissions_triplet",
            "project_id",
            "entity_id",
            "measurement_id",
        ),
        # Composite index for task-list queries
        Index(
            "ix_submissions_project_user",
            "project_id",
            "user_id",
        ),
    )

    # Client-supplied UUID — used as the natural PK (ADR-DB-003)
    submission_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)

    project_id: Mapped[str] = mapped_column(
        nullable=False,
        index=True,
    )
    # entity_type replaces the old submission_type column
    entity_type: Mapped[str] = mapped_column(nullable=False)

    # Identity triplet — uniquely versions a domain measurement
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    measurement_id: Mapped[uuid.UUID] = mapped_column(nullable=False)

    user_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    # Raw validated payload for audit trail
    raw_payload: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )

    # ── Relationships ─────────────────────────────────────────────────────────

    # 1:1 user state snapshot (replaces the old user_context JSONB blob)
    user_snapshot: Mapped[SubmissionUserSnapshot] = relationship(
        "SubmissionUserSnapshot",
        back_populates="submission",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # 1:N immutable scoring analysis (supports re-scoring — Case G)
    scoring_snapshots: Mapped[list[ScoringSnapshot]] = relationship(
        "ScoringSnapshot",
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="ScoringSnapshot.created_at.desc()",
    )

    # 1:N append-only lifecycle events
    ledger_entries: Mapped[list[StatusLedger]] = relationship(
        "StatusLedger",
        back_populates="submission",
        foreign_keys="StatusLedger.submission_id",
        cascade="all, delete-orphan",
        order_by="StatusLedger.created_at.desc()",
    )

    # Votes cast on this submission
    votes: Mapped[list[SubmissionVote]] = relationship(
        "SubmissionVote",
        back_populates="submission",
        cascade="all, delete-orphan",
        order_by="SubmissionVote.created_at.desc()",
    )

    # Outbox entries for webhook delivery
    outbox_events: Mapped[list[WebhookOutbox]] = relationship(
        "WebhookOutbox",
        back_populates="submission",
        cascade="all, delete-orphan",
    )
