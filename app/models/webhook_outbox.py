"""
WebhookOutbox ORM model.

Implements the Transactional Outbox Pattern (ADR-DB-006, Rule 11).
When a submission is auto-finalized by the governance engine, a row is
inserted into this table within the *same* database transaction as the
status update.  A background poller then reads pending rows and delivers
HTTP callbacks to the client system.

The ``status`` lifecycle: pending → in_progress → delivered | failed → dead_letter.
``SELECT … FOR UPDATE SKIP LOCKED`` on the poller query prevents concurrent
workers from picking up the same row.

References
----------
* Database design: docs/architecture/05_database_design.md §1.2, §6 step 5
* Rule 11:         .junie/AGENTS.md — Event-Driven State Changes (Webhooks)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.submission import Submission


class OutboxStatus(StrEnum):
    """Delivery lifecycle states for a webhook outbox entry."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class WebhookOutbox(Base, TimestampMixin):
    """
    Persistent webhook delivery record (Transactional Outbox Pattern).

    ``payload`` holds the full serialised webhook event body so the poller
    can deliver it without reconstructing state from other tables.
    ``next_retry_at`` drives the retry schedule; the poller filters on
    ``next_retry_at <= now()`` to avoid hammering unavailable endpoints.
    ``max_attempts`` comes from the project config (Rule 3) and is
    snapshotted here at creation time.
    """

    __tablename__ = "webhook_outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    submission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("submissions.submission_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Full serialised webhook event body
    payload: Mapped[dict] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=OutboxStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Snapshotted at creation time from project config (Rule 3)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    # NULL means "deliver immediately"; set to future time on retry back-off
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Table-level indexes ───────────────────────────────────────────────────
    __table_args__ = (
        # Poller query: pending/failed rows ready for delivery
        Index(
            "ix_webhook_outbox_status_retry",
            "status",
            "next_retry_at",
        ),
    )

    # ── Relationship ──────────────────────────────────────────────────────────
    submission: Mapped[Submission] = relationship(
        "Submission",
        back_populates="outbox_events",
    )

    def __repr__(self) -> str:
        return (
            f"<WebhookOutbox id={self.id!s} "
            f"submission_id={self.submission_id!s} status={self.status!r}>"
        )


__all__ = ["OutboxStatus", "WebhookOutbox"]
