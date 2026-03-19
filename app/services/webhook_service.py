"""
Webhook service — Transactional Outbox for guaranteed delivery.

Sprint 5 (Lifecycle Ledger) changes
-------------------------------------
* Every ``status_ledger`` INSERT triggers ``enqueue_ledger_event`` —
  called within the *same* ``AsyncSession`` transaction so the outbox
  INSERT and the ledger INSERT commit or roll back atomically (Rule 11).
* Payload schema: ``StatusLedgerWebhookPayload`` (replaces the old
  ``WebhookFinalizationPayload`` / ``WebhookEvent`` pair).
* ``event_type`` distinguishes events; ``event_id`` = ``status_ledger.id``
  for client-side idempotency.
* Webhook URL is read from the project registry (``config.webhook_url``).
  If ``webhook_url`` is None the event is silently skipped (test/dev).

Delivery
--------
``attempt_delivery`` performs the actual HTTP POST using ``httpx``.
On failure the entry transitions to ``'failed'`` with retry metadata.
After ``max_attempts`` failures it transitions to ``'dead'``.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §5
* Rule 11:      Event-driven state transitions via Transactional Outbox
* Rule 9:       Services never import fastapi / never raise HTTPException
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.webhook_outbox import WebhookOutbox
from app.schemas.webhooks import StatusLedgerWebhookPayload

logger = logging.getLogger(__name__)

# Retry back-off delay between attempts (in seconds)
_RETRY_DELAY_SECONDS = 60


async def enqueue_ledger_event(
    *,
    ledger_entry_id: UUID,
    submission_id: UUID,
    project_id: str,
    entity_id: UUID,
    entity_type: str,
    new_status: str,
    supersede_reason: str | None,
    trust_delta: int,
    confidence_score: float,
    occurred_at: datetime,
    event_type: str,
    webhook_url: str | None,
    db: AsyncSession,
) -> None:
    """
    Build a ``StatusLedgerWebhookPayload`` and INSERT it into ``webhook_outbox``.

    Must be called within the same ``AsyncSession`` transaction as the
    ``status_ledger`` INSERT (Transactional Outbox pattern — Rule 11).

    If ``webhook_url`` is None (e.g. project has no configured endpoint in
    dev/test) the function logs a debug message and returns without writing
    to the outbox, avoiding spurious rows in tests that don't configure URLs.
    """
    if not webhook_url:
        logger.debug(
            "No webhook_url configured for project '%s' — skipping outbox enqueue.",
            project_id,
        )
        return

    payload = StatusLedgerWebhookPayload(
        event_id=ledger_entry_id,
        event_type=event_type,
        occurred_at=occurred_at,
        submission_id=submission_id,
        project_id=project_id,
        entity_id=entity_id,
        entity_type=entity_type,
        new_status=new_status,
        supersede_reason=supersede_reason,
        trust_delta=trust_delta,
        confidence_score=confidence_score,
    )

    entry = WebhookOutbox(
        id=uuid4(),
        submission_id=submission_id,
        event_type=event_type,
        payload=payload.model_dump(mode="json"),
        status="pending",
    )
    db.add(entry)
    await db.flush()

    logger.info(
        "Webhook enqueued",
        extra={
            "event_type": event_type,
            "submission_id": str(submission_id),
            "ledger_entry_id": str(ledger_entry_id),
            "webhook_url": webhook_url,
        },
    )


async def get_pending_entries(db: AsyncSession) -> list[WebhookOutbox]:
    """Return all outbox entries ready for delivery attempt."""
    now = datetime.now(timezone.utc)
    stmt = select(WebhookOutbox).where(
        WebhookOutbox.status.in_(["pending", "failed"]),
        (WebhookOutbox.next_retry_at == None) | (WebhookOutbox.next_retry_at <= now),  # noqa: E711
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def attempt_delivery(entry_id: UUID, db: AsyncSession) -> bool:
    """
    Attempt to deliver a single outbox entry via HTTP POST.

    Transitions:
    * Success (2xx)           → ``'sent'``
    * Failure (non-2xx / exc) → ``'failed'`` with next_retry_at back-off
    * Exceeded max_attempts   → ``'dead'``

    Returns True on successful delivery, False otherwise.
    """
    entry = await db.get(WebhookOutbox, entry_id)
    if entry is None or entry.status == "sent":
        return False

    # Resolve delivery URL from payload (stored at enqueue time as fallback)
    webhook_url: str | None = entry.payload.get("_webhook_url")
    if not webhook_url:
        # Mark dead — no URL to deliver to
        entry.status = "dead"
        entry.last_error = "No webhook_url in payload metadata."
        await db.flush()
        return False

    entry.status = "processing"
    entry.attempts = (entry.attempts or 0) + 1
    await db.flush()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                webhook_url,
                json=entry.payload,
                headers={"Content-Type": "application/json"},
            )
        response.raise_for_status()
        entry.status = "sent"
        logger.info(
            "Webhook delivered",
            extra={
                "entry_id": str(entry_id),
                "submission_id": str(entry.submission_id),
                "event_type": entry.event_type,
            },
        )
        await db.flush()
        return True

    except Exception as exc:
        entry.last_error = str(exc)
        if entry.attempts >= entry.max_attempts:
            entry.status = "dead"
            logger.error(
                "Webhook permanently failed (dead)",
                extra={"entry_id": str(entry_id), "error": str(exc)},
            )
        else:
            entry.status = "failed"
            entry.next_retry_at = datetime.now(timezone.utc) + timedelta(
                seconds=_RETRY_DELAY_SECONDS * entry.attempts
            )
            logger.warning(
                "Webhook delivery failed — will retry",
                extra={
                    "entry_id": str(entry_id),
                    "attempt": entry.attempts,
                    "next_retry_at": entry.next_retry_at.isoformat(),
                    "error": str(exc),
                },
            )
        await db.flush()
        return False
