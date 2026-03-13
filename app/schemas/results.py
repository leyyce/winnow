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

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator


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

    These are advisory values — the client uses them to route a submission into
    one of three contiguous regions on the 0-100 Confidence Score scale:

      ┌─────────────────────────────────────────────────────────┐
      │  [0 … manual_review_min)  →  auto-reject  (implicit)   │
      │  [manual_review_min … auto_approve_min)  →  review      │
      │  [auto_approve_min … 100]  →  auto-approve              │
      └─────────────────────────────────────────────────────────┘

    **Why 2 boundaries instead of 3?**
    A 0-100 integer scale divided into 3 contiguous, non-overlapping regions
    requires exactly 2 boundary values.  A 3-value system (approve / review /
    reject) allows *gaps* (scores between reject and review that fall into no
    region) and *overlaps* (approve == reject), both of which produce ambiguous
    routing decisions.  Two boundaries are mathematically sufficient and
    eliminate that class of misconfiguration entirely.

    The ``reject`` region is implicit: any score below ``manual_review_min``
    is auto-rejected by the client.  Winnow does not return a third field
    because it would be redundant (``reject_max = manual_review_min - 1``) and
    could create the illusion that a gap between review and reject is
    permissible.

    Cross-field constraint: ``auto_approve_min >= manual_review_min``.
    """

    auto_approve_min: int = Field(
        ge=0,
        le=100,
        description=(
            "Scores at or above this value may be auto-approved by the client. "
            "Integer on the 0-100 Confidence Score scale."
        ),
    )
    manual_review_min: int = Field(
        ge=0,
        le=100,
        description=(
            "Scores at or above this value (but below `auto_approve_min`) are queued "
            "for manual review.  Scores below this value are implicitly auto-rejected "
            "by the client — no third field is returned."
        ),
    )

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "ThresholdConfig":
        """Enforce auto_approve_min >= manual_review_min to prevent routing gaps."""
        if self.auto_approve_min < self.manual_review_min:
            raise ValueError(
                f"'auto_approve_min' ({self.auto_approve_min}) must be >= "
                f"'manual_review_min' ({self.manual_review_min})"
            )
        return self


class RequiredValidations(BaseModel):
    """
    Governance 'Target State' — the review requirements Winnow computed for this submission.

    Returned in the initial scoring response so the client can immediately render
    its review queue and access controls without a second round-trip. Winnow is the
    Governance Authority; the client acts as a Task Client rendering whatever Winnow
    permits.

    Role-weights pattern (Task 2 — Dynamic Governance):
    Instead of a single ``min_validators`` counter and a hard ``required_role``
    string, governance thresholds are expressed as a ``threshold_score`` (the
    minimum accumulated weight needed to finalise) and a ``role_weights`` dict
    (mapping role name → integer weight contribution per vote).  The voting
    service sums the weights of all eligible approve/reject votes; when the sum
    reaches ``threshold_score`` the submission is auto-finalised.

    Example — "2 citizens OR 1 expert":
        threshold_score=2, role_weights={"citizen": 1, "expert": 2}
        → two citizen approvals (1+1=2) OR one expert approval (2) both meet
          the threshold without any hardcoded role-check in the service layer.
    """

    threshold_score: int = Field(
        ge=1,
        description=(
            "Minimum accumulated role-weight needed to finalise a submission. "
            "The voting service sums role_weights[voter_role] for each eligible "
            "vote; when approve_sum or reject_sum >= threshold_score the submission "
            "transitions to 'approved' or 'rejected' respectively."
        ),
    )
    role_weights: dict[str, int] = Field(
        description=(
            "Mapping of reviewer role → integer weight contributed per vote. "
            "Roles absent from this dict (or with weight 0) are ineligible to "
            "vote on this submission, replacing the old hard required_role check."
        ),
    )
    required_min_trust: int = Field(
        ge=0,
        description="Minimum trust level a reviewer must hold to be eligible. Scale is project-specific.",
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
    status: Literal[
        "pending_review", "pending_finalization", "approved", "rejected", "superseded",
    ] = Field(
        description=(
            "Current lifecycle state of the submission. "
            "'pending_review' = awaiting votes (Governance Engine flow). "
            "'pending_finalization' = legacy status (pre-voting flow). "
            "'approved'/'rejected' = terminal states set by vote threshold. "
            "'superseded' = replaced by a corrected submission."
        ),
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
    created_at: AwareDatetime = Field(
        description="ISO-8601 timestamp of when Winnow persisted this scoring result.",
    )
