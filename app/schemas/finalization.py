"""
Finalization request and response schemas.

These models define the shape of data exchanged during the PATCH
/api/v1/submissions/{id}/final-status lifecycle step, where the client
delivers the ground-truth outcome after expert or community review.

References
----------
* API contract: docs/architecture/03_api_contracts.md §3b
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class FinalizationRequest(BaseModel):
    """
    Body sent by the client when closing the feedback loop on a submission.

    The ``final_status`` drives the Trust Advisor (Stage 4 output): 'approved'
    triggers a reward delta; 'rejected' triggers a penalty delta.
    """

    final_status: Literal["approved", "rejected"] = Field(
        description="Ground-truth outcome as determined by the expert or community review.",
    )
    reviewed_by: str | None = Field(
        default=None,
        description="Identifier of the reviewer who made the final decision; for audit purposes.",
    )
    review_note: str | None = Field(
        default=None,
        description="Optional human-readable explanation accompanying the final decision.",
    )


class TrustAdjustment(BaseModel):
    """
    Trust adjustment recommendation returned to the client after finalization.

    Winnow advises; the client decides whether to apply the delta. The client
    MUST apply only the ``recommended_delta`` atomically using the returned
    bounds to clamp the result:
        new_level = CLAMP(trust_level + recommended_delta,
                          project_min_trust, project_max_trust)
    """

    user_id: UUID = Field(
        description="Stable user identifier from the client system.",
    )
    recommended_delta: int = Field(
        description="Trust delta to apply: positive = reward, negative = penalty.",
    )
    reason: str = Field(
        min_length=1,
        description="Human-readable explanation of why this delta was computed.",
    )
    current_trust_level: int = Field(
        ge=0,
        description="Trust level as received on the wire at original submission time.",
    )
    project_min_trust: int = Field(
        ge=0,
        description="Project-configured lower bound for the trust scale.",
    )
    project_max_trust: int = Field(
        ge=0,
        description="Project-configured upper bound for the trust scale.",
    )


class FinalizationResponse(BaseModel):
    """
    Full finalization outcome returned after PATCH /submissions/{id}/final-status.

    Includes the original confidence score for reference and a trust adjustment
    recommendation for the client to apply to the submitter's trust level.
    """

    submission_id: UUID = Field(
        description="Echo of the submission UUID that was finalized.",
    )
    final_status: Literal["approved", "rejected"] = Field(
        description="Confirmed ground-truth outcome.",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Original Confidence Score computed at submission time, for reference.",
    )
    trust_adjustment: TrustAdjustment = Field(
        description="Advisory trust delta for the client to apply to the submitter's trust level.",
    )
    finalized_at: datetime = Field(
        description="ISO-8601 timestamp of when Winnow processed the finalization.",
    )
