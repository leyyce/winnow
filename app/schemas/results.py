"""
Response schemas for scoring outcomes returned by Winnow.

These models define the shape of data sent back to the client after:
  - POST /api/v1/submissions   → ScoringResultResponse (201 Created)
  - GET  /api/v1/results/{id}  → ScoringResultResponse (200 OK)

Finalization response schemas (PATCH /submissions/{id}/final-status) live in
app/schemas/finalization.py and are intentionally kept separate to respect the
two-phase lifecycle: initial scoring vs. ground-truth finalization.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class RuleBreakdown(BaseModel):
    """Per-rule contribution to the overall Confidence Score."""

    rule: str = Field(
        min_length=1,
        description="Machine-readable rule identifier, e.g. 'height_factor'.",
    )
    weight: float = Field(
        ge=0.0,
        le=1.0,
        description="Fractional weight assigned to this rule in the pipeline (0–1).",
    )
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Normalised rule output (0–1) before weighting.",
    )
    weighted_score: float = Field(
        ge=0.0,
        description="Contribution to the Confidence Score: score × weight × 100.",
    )
    details: str | None = Field(
        default=None,
        description="Optional human-readable explanation of how the score was derived.",
    )


class ThresholdConfig(BaseModel):
    """
    Per-project Confidence Score thresholds returned alongside every result.

    These are advisory values — the client uses them to route the submission
    (auto-approve / queue for review / auto-reject) without hard-coding thresholds
    on its own side. Winnow advises; the client decides.
    """

    approve: float = Field(
        ge=0.0,
        le=100.0,
        description="Scores at or above this value may be auto-approved by the client.",
    )
    review: float = Field(
        ge=0.0,
        le=100.0,
        description="Scores at or above this value (but below `approve`) require manual review.",
    )
    reject: float = Field(
        ge=0.0,
        le=100.0,
        description="Scores below this value may be auto-rejected by the client.",
    )


class RequiredValidations(BaseModel):
    """
    Governance 'Target State' — the review requirements Winnow computed for this submission.

    Returned in the initial scoring response so the client can immediately render
    its review queue and access controls without a second round-trip. Winnow is the
    Governance Authority; the client acts as a Task Client rendering whatever Winnow
    permits.
    """

    min_validators: int = Field(
        ge=1,
        description="Minimum number of distinct reviewers that must validate this submission.",
    )
    required_min_trust: int = Field(
        ge=0,
        description="Minimum trust level a reviewer must hold to be eligible. Scale is project-specific.",
    )
    required_role: str | None = Field(
        default=None,
        description="Role constraint for eligible reviewers (e.g. 'expert'). None = any role.",
    )
    review_tier: str = Field(
        min_length=1,
        description=(
            "Human-readable review tier label, e.g. 'auto_approve', "
            "'peer_review', 'community_review', 'expert_review'."
        ),
    )


class ScoringResultResponse(BaseModel):
    """
    Full scoring outcome returned after a successful POST /api/v1/submissions.

    The status is always 'pending_finalization' on initial creation; it transitions
    to 'approved' or 'rejected' only after the client sends a finalization signal
    (PATCH /submissions/{id}/final-status).
    """

    submission_id: UUID = Field(
        description="Echo of the client-supplied submission UUID.",
    )
    project_id: str = Field(
        min_length=1,
        description="Project this submission belongs to.",
    )
    status: Literal["pending_finalization", "approved", "rejected"] = Field(
        description="Current lifecycle state of the submission.",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Aggregate Confidence Score (0–100) computed by the scoring pipeline.",
    )
    breakdown: list[RuleBreakdown] = Field(
        description="Per-rule scoring contributions that sum to the confidence_score.",
    )
    required_validations: RequiredValidations = Field(
        description="Governance Target State — review requirements for this submission.",
    )
    thresholds: ThresholdConfig = Field(
        description="Project-specific score thresholds for client-side routing decisions.",
    )
    created_at: datetime = Field(
        description="ISO-8601 timestamp of when Winnow persisted this scoring result.",
    )
