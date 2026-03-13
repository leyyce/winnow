"""
Webhook service — manages the Transactional Outbox for guaranteed delivery.

Implements the outbox pattern using an in-memory store (stub) until the DB
layer is added in Sprint 3. When a submission is auto-finalized via vote
threshold evaluation, a webhook event is created atomically and queued for
asynchronous delivery to the client's webhook URL.

References
----------
* Architecture: docs/architecture/03_api_contracts.md §10
* Database design: docs/architecture/05_database_design.md (webhook_outbox table)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from app.schemas.voting import VoteTally

logger = logging.getLogger(__name__)


# ── Outbox entry states ──────────────────────────────────────────────────────

class OutboxStatus(StrEnum):
    """Delivery lifecycle states for a webhook outbox entry."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


# ── In-memory outbox store (stub until Sprint 3 DB layer) ────────────────────

@dataclass
class OutboxEntry:
    """In-memory representation of a webhook outbox row."""

    id: UUID
    submission_id: UUID
    event_type: str
    payload: dict                   # serialised webhook event body
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    max_attempts: int = 5           # default; will come from project config in Sprint 3
    next_retry_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# Global in-memory outbox: delivery_id → OutboxEntry
_outbox: dict[UUID, OutboxEntry] = {}


# ── Public API ────────────────────────────────────────────────────────────────

async def queue_finalization_webhook(
    *,
    submission_id: UUID,
    project_id: str,
    final_status: str,
    confidence_score: float,
    user_context: object | None,
    tally: VoteTally,
) -> UUID:
    """
    Create an outbox entry for a finalization webhook event.

    In Sprint 3 this will be an INSERT within the same DB transaction that
    finalizes the submission (atomic outbox pattern). For now it writes to
    the in-memory store.

    Parameters
    ----------
    submission_id : UUID
        The finalized submission.
    project_id : str
        Project identifier for routing.
    final_status : str
        Terminal status (``"approved"`` or ``"rejected"``).
    confidence_score : float
        Original Confidence Score.
    user_context : object | None
        Original submitter context (None in stub mode; resolved from DB in Sprint 3).
    tally : VoteTally
        Final vote counts at finalization time.

    Returns
    -------
    UUID
        The ``delivery_id`` of the created outbox entry.
    """
    delivery_id = uuid4()

    event_payload = {
        "event": "submission.finalized",
        "delivery_id": str(delivery_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "submission_id": str(submission_id),
            "project_id": project_id,
            "final_status": final_status,
            "confidence_score": confidence_score,
            "trust_adjustment": None,  # computed from DB in Sprint 3
            "vote_summary": {
                "approve": tally.approve,
                "reject": tally.reject,
                "total": tally.approve + tally.reject,
            },
        },
    }

    entry = OutboxEntry(
        id=delivery_id,
        submission_id=submission_id,
        event_type="submission.finalized",
        payload=event_payload,
    )
    _outbox[delivery_id] = entry

    logger.info(
        "Webhook queued",
        extra={
            "delivery_id": str(delivery_id),
            "submission_id": str(submission_id),
            "event_type": "submission.finalized",
            "final_status": final_status,
        },
    )

    return delivery_id


def get_pending_entries() -> list[OutboxEntry]:
    """Return all outbox entries awaiting delivery (pending or failed with retry due)."""
    now = datetime.now(timezone.utc)
    return [
        entry
        for entry in _outbox.values()
        if entry.status == OutboxStatus.PENDING
        or (
            entry.status == OutboxStatus.FAILED
            and entry.next_retry_at is not None
            and entry.next_retry_at <= now
        )
    ]


async def attempt_delivery(entry_id: UUID) -> bool:
    """
    Attempt to deliver a webhook event.

    This is a **stub** — no actual HTTP call is made. In Sprint 3 this will
    use ``httpx.AsyncClient`` to POST to the client's webhook URL with
    exponential backoff and retry tracking.

    Returns
    -------
    bool
        True if delivery succeeded (stubbed as always-True for now).
    """
    entry = _outbox.get(entry_id)
    if entry is None:
        return False

    entry.status = OutboxStatus.IN_PROGRESS
    entry.attempts += 1
    entry.updated_at = datetime.now(timezone.utc)

    # STUB: simulate successful delivery
    entry.status = OutboxStatus.DELIVERED
    entry.updated_at = datetime.now(timezone.utc)

    logger.info(
        "Webhook delivered (stub)",
        extra={
            "delivery_id": str(entry_id),
            "submission_id": str(entry.submission_id),
            "attempts": entry.attempts,
        },
    )

    return True


def get_outbox_entry(entry_id: UUID) -> OutboxEntry | None:
    """Look up an outbox entry by delivery ID."""
    return _outbox.get(entry_id)


def clear_outbox() -> None:
    """Reset the in-memory outbox. Used by tests only."""
    _outbox.clear()
