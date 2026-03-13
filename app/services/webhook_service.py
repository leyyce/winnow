"""
Webhook service — DB-backed Transactional Outbox for guaranteed delivery.

Replaces the Sprint 2.6 in-memory ``_outbox`` dict with real DB operations.
``queue_finalization_webhook`` is called from ``voting_service.cast_vote``
within the *same* ``AsyncSession`` transaction — the outbox INSERT and the
submission status UPDATE commit or roll back together, implementing the
Transactional Outbox pattern (ADR-DB-006, Rule 11).

The delivery poller (``get_pending_entries`` + ``attempt_delivery``) is a
stub in this sprint — actual HTTP delivery via ``httpx`` is deferred to
Sprint 4.  The outbox rows are correctly persisted and queryable.

References
----------
* Architecture: docs/architecture/03_api_contracts.md §10
* Database:     docs/architecture/05_database_design.md §6 step 11
* Rule 11:      Event-Driven State Changes (Webhooks)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_outbox import OutboxStatus, WebhookOutbox
from app.schemas.voting import VoteTally

logger = logging.getLogger(__name__)


async def queue_finalization_webhook(
    *,
    submission_id: UUID,
    project_id: str,
    final_status: str,
    confidence_score: float,
    user_context: dict | None,
    tally: VoteTally,
    db: AsyncSession,
) -> UUID:
    """
    Insert a webhook outbox row within the caller's transaction.

    Must be called inside an active ``AsyncSession`` transaction (i.e. from
    ``voting_service.cast_vote``) so that the outbox INSERT and submission
    status UPDATE are atomic.  The ``db`` session is *not* committed here —
    commit is deferred to the ``get_db`` FastAPI dependency.

    Parameters
    ----------
    submission_id:
        UUID of the finalized submission.
    project_id:
        Project identifier for event routing.
    final_status:
        Terminal status — ``"approved"`` or ``"rejected"``.
    confidence_score:
        Original Confidence Score at submission time.
    user_context:
        Submitter context snapshot (dict from JSONB column, or None).
    tally:
        Final vote counts at finalization time.
    db:
        Caller's active ``AsyncSession``.

    Returns
    -------
    UUID
        The ``id`` of the created ``WebhookOutbox`` row.
    """
    delivery_id = uuid4()
    event_payload: dict = {
        "event": "submission.finalized",
        "delivery_id": str(delivery_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {
            "submission_id": str(submission_id),
            "project_id": project_id,
            "final_status": final_status,
            "confidence_score": confidence_score,
            "trust_adjustment": None,  # computed in Sprint 4 from user history
            "vote_summary": {
                "approve": tally.approve,
                "reject": tally.reject,
                "total": tally.approve + tally.reject,
            },
        },
    }

    outbox_row = WebhookOutbox(
        id=delivery_id,
        submission_id=submission_id,
        event_type="submission.finalized",
        payload=event_payload,
        status=OutboxStatus.PENDING,
    )
    db.add(outbox_row)
    await db.flush()

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


async def get_pending_entries(db: AsyncSession) -> list[WebhookOutbox]:
    """
    Return all outbox rows awaiting delivery (pending or failed with retry due).

    Used by the background poller.  Rows with ``next_retry_at`` in the future
    are excluded — they are not yet ready for re-delivery.

    Parameters
    ----------
    db:
        ``AsyncSession`` for the poller worker (separate transaction from writes).
    """
    now = datetime.now(timezone.utc)
    stmt = select(WebhookOutbox).where(
        (WebhookOutbox.status == OutboxStatus.PENDING)
        | (
            (WebhookOutbox.status == OutboxStatus.FAILED)
            & (WebhookOutbox.next_retry_at <= now)
        )
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def attempt_delivery(entry_id: UUID, db: AsyncSession) -> bool:
    """
    Attempt to deliver a webhook event.

    **Stub** — no actual HTTP call is made.  Sprint 4 will replace this stub
    with an ``httpx.AsyncClient`` POST with exponential back-off and retry
    tracking.

    The outbox row status is updated to ``DELIVERED`` within the caller's
    session.  The caller is responsible for committing.

    Returns
    -------
    bool
        ``True`` if delivery succeeded (stubbed as always-True for now).
    """
    stmt = select(WebhookOutbox).where(WebhookOutbox.id == entry_id).with_for_update()
    entry = (await db.execute(stmt)).scalar_one_or_none()
    if entry is None:
        return False

    entry.status = OutboxStatus.IN_PROGRESS
    entry.attempts += 1
    await db.flush()

    # STUB: simulate successful delivery (Sprint 4 will make a real HTTP call)
    entry.status = OutboxStatus.DELIVERED
    await db.flush()

    logger.info(
        "Webhook delivered (stub)",
        extra={
            "delivery_id": str(entry_id),
            "submission_id": str(entry.submission_id),
            "attempts": entry.attempts,
        },
    )
    return True
