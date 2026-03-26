"""
Webhook event schemas — Sprint 5 Lifecycle Ledger.

Every ``status_ledger`` INSERT triggers one ``webhook_outbox`` entry.
The external project (Laravel) is the Single Source of Truth consumer:
it must update its local state and user trust *exclusively* via these webhooks.
The API response from POST /submissions is for UI feedback only.

Every payload includes:
* ``event_id``    — the ``status_ledger.id`` UUID for client-side idempotency.
* ``event_type``  — differentiates the trigger (see vocabulary below).
* ``occurred_at`` — the ledger entry's ``created_at`` for ordering.
* ``trust_delta`` — the incremental trust change the client should apply.
* ``supersede_reason`` — the reason code (null for the initial entry).

Event type vocabulary
---------------------
submission.created        — initial pending_review entry
submission.auto_approved  — automated threshold approval
submission.auto_rejected  — automated threshold rejection
submission.superseded     — chain voided due to user edit (Case B)
submission.withdrawn      — user withdrawal via PATCH /withdraw (Case C)
submission.approved       — community voting concluded with approval
submission.rejected       — community voting concluded with rejection
submission.admin_overridden — admin override forced terminal state
submission.rescored       — re-scoring event created new snapshot

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §5 (Outbox Pattern)
* Database:     docs/architecture/05_database_design.md §3.4
* Rule 11:      Event-driven state transitions via Transactional Outbox
"""
from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class StatusLedgerWebhookPayload(BaseModel):
    """
    Payload written to ``webhook_outbox`` for every ``status_ledger`` INSERT.

    The client system (Laravel) consumes these events to keep its local state
    and user trust in sync with Winnow's authoritative governance decisions.

    ``event_id`` equals the ``status_ledger.id`` that triggered this event —
    the client should use it as an idempotency key to safely deduplicate
    retried deliveries.

    ``occurred_at`` equals the ledger entry's ``created_at`` timestamp —
    the client should use it to apply last-write-wins ordering when events
    arrive out of sequence due to webhook retry jitter.

    ``trust_delta`` is the *incremental* change; the client adds this to the
    user's running trust total.  It may be negative (e.g. admin reversal).
    """

    event_id: UUID = Field(
        description="= status_ledger.id — idempotency key for client deduplication.",
    )
    event_type: str = Field(
        min_length=1,
        description=(
            "Event classifier, e.g. 'submission.created', 'submission.approved'. "
            "See module docstring for the full vocabulary."
        ),
    )
    occurred_at: AwareDatetime = Field(
        description="= status_ledger.created_at — ordering anchor for the client.",
    )
    project_id: str = Field(
        min_length=1,
        description="Project identifier.",
    )
    submission_id: UUID = Field(
        description="UUID of the submission this event relates to.",
    )
    entity_type: str = Field(
        description="Entity type, e.g. 'tree'.",
    )
    entity_id: UUID = Field(
        description="Domain entity UUID (part of the identity triplet).",
    )
    measurement_id: UUID = Field(
        description="Measurement UUID (part of the identity triplet).",
    )
    new_status: str = Field(
        description="The lifecycle status recorded in the ledger entry.",
    )
    supersedes: UUID | None = Field(
        default=None,
        description="ID of the ledger entry this row replaces (backward pointer).",
    )
    supersede_reason: str | None = Field(
        default=None,
        description="Why this ledger entry was created. Null for the initial entry.",
    )
    trust_delta: int = Field(
        default=0,
        description=(
            "Incremental trust change for this event. "
            "Client adds this to the submitting user's running trust total."
        ),
    )
    confidence_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Confidence score from the scoring snapshot linked to this ledger entry.",
    )
