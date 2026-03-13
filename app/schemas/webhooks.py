"""
Webhook event schemas for the Transactional Outbox pattern.

These models define the shape of webhook payloads that Winnow delivers to
the client (Laravel) when a submission is auto-finalized via vote threshold
evaluation. The outbox worker serialises these models to JSON before delivery.

References
----------
* API contract: docs/architecture/03_api_contracts.md §10
* Database design: docs/architecture/05_database_design.md (webhook_outbox table)
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from app.schemas.finalization import TrustAdjustment
from app.schemas.voting import VoteTally


class WebhookFinalizationPayload(BaseModel):
    """
    Domain payload embedded inside a webhook event when a submission is
    auto-finalized by the governance engine.
    """

    submission_id: UUID = Field(
        description="UUID of the finalized submission.",
    )
    project_id: str = Field(
        min_length=1,
        description="Project this submission belongs to.",
    )
    final_status: Literal["approved", "rejected"] = Field(
        description="Terminal status reached via vote threshold evaluation.",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Original Confidence Score computed at submission time.",
    )
    trust_adjustment: TrustAdjustment = Field(
        description="Trust delta recommendation for the original submitter.",
    )
    vote_summary: VoteTally = Field(
        description="Final vote tally at the moment of finalization.",
    )


class WebhookEvent(BaseModel):
    """
    Top-level webhook envelope delivered to the client's webhook URL.

    Each event carries a unique ``delivery_id`` for idempotent processing
    on the client side.
    """

    event: Literal["submission.finalized"] = Field(
        description="Event type identifier.",
    )
    delivery_id: UUID = Field(
        description="Unique delivery identifier for client-side idempotency.",
    )
    timestamp: AwareDatetime = Field(
        description="ISO-8601 timestamp of when the event was created.",
    )
    payload: WebhookFinalizationPayload = Field(
        description="Event-specific payload data.",
    )
