"""
StatusLedger ORM model.

The append-only lifecycle log for every submission state transition.
Every row is an INSERT — no row is ever UPDATEd or DELETEd (except via
cascade when a submission is hard-deleted, which is not a normal operation).

Backward-chaining immutability
-------------------------------
The ``supersedes`` column is a self-referencing FK that points to the
``status_ledger.id`` of the entry this row *replaces*.  This means:

* Old entries are NEVER touched.
* The "active" entry for a submission is the row with the highest
  ``created_at`` for that ``submission_id`` (its ``id`` does not appear in
  any other row's ``supersedes`` field).
* The full lifecycle history is reconstructable by following the
  ``supersedes`` chain backwards.

Trust delta algorithm
---------------------
``trust_delta`` stores the *incremental* change needed to reach the target
trust level, computed as::

    trust_delta = target_trust(new_status) - SUM(all prior trust_deltas in lineage)

This allows full reversal (e.g. admin overwrite of an approved submission)
without a separate audit table — just follow the chain and sum.

References
----------
* Database design: docs/architecture/05_database_design.md §1.4
* Rule 10:         .junie/AGENTS.md — Immutable Submissions & Append-Only State
"""
from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.scoring_snapshot import ScoringSnapshot
    from app.models.submission import Submission


class StatusLedgerStatus(StrEnum):
    """Valid lifecycle states stored on each StatusLedger row."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    VOIDED = "voided"


class SupersedeReason(StrEnum):
    """Valid reasons for a superseding ledger entry."""

    EDITED = "edited"
    DELETED = "deleted"
    VOTING_CONCLUDED = "voting_concluded"
    AUTO_APPROVE = "auto_approve"
    AUTO_REJECT = "auto_reject"
    ADMIN_OVERWRITE = "admin_overwrite"
    RE_SCORED = "re-scored"


# Terminal states: once in these, normal user actions cannot create new entries
TERMINAL_STATES: frozenset[str] = frozenset(
    {StatusLedgerStatus.APPROVED, StatusLedgerStatus.REJECTED, StatusLedgerStatus.VOIDED}
)


class StatusLedger(Base, TimestampMixin):
    """
    Single lifecycle event row in the append-only status ledger.

    One row per state transition.  The active state for a submission is
    always the row with the latest ``created_at`` for that ``submission_id``.

    ``supersedes`` — backward pointer to the ``id`` of the entry this row
    replaces.  NULL for the first entry in any chain (or cross-chain anchor).
    The self-referencing FK uses ``use_alter=True`` to avoid circular DDL
    dependency during table creation.

    ``trust_delta`` — incremental trust change for this event.  The running
    total for a user is ``SUM(trust_delta)`` across all ledger entries linked
    to that user's submissions.
    """

    __tablename__ = "status_ledger"

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'voided')",
            name="ck_status_ledger_status",
        ),
        CheckConstraint(
            "supersede_reason IS NULL OR supersede_reason IN ("
            "'edited', 'deleted', 'voting_concluded', "
            "'auto_approve', 'auto_reject', 'admin_overwrite', 're-scored')",
            name="ck_status_ledger_supersede_reason",
        ),
        Index(
            "ix_status_ledger_submission_created",
            "submission_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scoring_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scoring_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        nullable=False,
        default=StatusLedgerStatus.PENDING_REVIEW,
        index=True,
    )
    # Incremental trust change for this event (may be negative for reversals)
    trust_delta: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    # Backward pointer to the entry this row supersedes (NULL = first in chain)
    supersedes: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "status_ledger.id",
            use_alter=True,
            name="fk_status_ledger_supersedes_self",
        ),
        nullable=True,
        default=None,
    )
    supersede_reason: Mapped[str | None] = mapped_column(nullable=True, default=None)

    # ── Relationships ─────────────────────────────────────────────────────────
    submission: Mapped[Submission] = relationship(
        "Submission",
        back_populates="ledger_entries",
        foreign_keys=[submission_id],
    )
    scoring_snapshot: Mapped[ScoringSnapshot] = relationship(
        "ScoringSnapshot",
        back_populates="ledger_entries",
        foreign_keys=[scoring_snapshot_id],
    )
    # Self-referential: the entry this row replaces (nullable)
    superseded_entry: Mapped[StatusLedger | None] = relationship(
        "StatusLedger",
        foreign_keys=[supersedes],
        remote_side="StatusLedger.id",
        uselist=False,
    )
