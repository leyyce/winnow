"""
Response schemas for scoring outcomes returned by Winnow.

Sprint 5 (Lifecycle Ledger) changes
-------------------------------------
* ``RequiredValidations``: upgraded from flat role_weights to structured
  ``role_configs`` / ``default_config`` / ``blocked_roles`` governance model.
* ``ScoringResultResponse``: removed ``'superseded'`` status (replaced by
  ``'voided'`` + ``supersede_reason``); added ``supersede_reason``,
  ``trust_delta``, ``current_user_vote``, ``ledger_entry_id``.
* ``StatusLedgerEntryResponse``: new schema for individual ledger entries.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.schemas.voting import ActiveVoteItem


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

    Two boundaries divide the 0-100 scale into three contiguous regions:
      [0 … manual_review_min)          → auto-reject (implicit)
      [manual_review_min … auto_approve_min) → manual review
      [auto_approve_min … 100]          → auto-approve

    Cross-field constraint: ``auto_approve_min >= manual_review_min``.
    """

    auto_approve_min: int = Field(
        ge=0,
        le=100,
        description="Scores at or above this value trigger auto-approval.",
    )
    manual_review_min: int = Field(
        ge=0,
        le=100,
        description=(
            "Scores at or above this value (but below auto_approve_min) go to "
            "manual review.  Scores below are implicitly auto-rejected."
        ),
    )

    @model_validator(mode="after")
    def _thresholds_ordered(self) -> "ThresholdConfig":
        if self.auto_approve_min < self.manual_review_min:
            raise ValueError(
                f"'auto_approve_min' ({self.auto_approve_min}) must be >= "
                f"'manual_review_min' ({self.manual_review_min})"
            )
        return self


class RoleConfig(BaseModel):
    """Weight and minimum trust required for a specific role."""

    weight: int = Field(ge=0, description="Vote weight contributed per vote. 0 = ineligible (contributes nothing to tally).")
    min_trust: int = Field(ge=0, description="Minimum trust level required to vote.")


class RequiredValidations(BaseModel):
    """
    Governance 'Target State' — the review requirements Winnow computed for
    this submission.  Stored as a JSONB snapshot so the VotingService can
    evaluate eligibility from historical data without re-running governance.

    Sprint 5 upgrade: structured ``role_configs`` / ``default_config`` /
    ``blocked_roles`` replace the flat ``role_weights`` + ``required_min_trust``
    fields.  This enables per-role trust minimums and absolute exclusion of
    blocked roles.
    """

    threshold_score: int = Field(
        ge=1,
        description=(
            "Minimum accumulated role-weight needed to finalise a submission. "
            "The voting service sums role_configs[voter_role].weight for each "
            "eligible vote; when approve_sum or reject_sum >= threshold_score "
            "the submission transitions to 'approved' or 'rejected'."
        ),
    )
    role_configs: dict[str, RoleConfig] = Field(
        description=(
            "Per-role weight and min_trust map.  Roles present here use their "
            "specific config; roles absent fall back to default_config."
        ),
    )
    default_config: RoleConfig | None = Field(
        description=(
            "Fallback config for roles not listed in role_configs.  "
            "Applied to any role not in role_configs and not in blocked_roles."
        ),
    )
    blocked_roles: list[str] = Field(
        default_factory=list,
        description=(
            "Roles that are absolutely ineligible to vote.  Takes precedence "
            "over both role_configs and default_config."
        ),
    )
    review_tier: str = Field(
        min_length=1,
        description="Human-readable tier label, e.g. 'peer_review', 'expert_review'.",
    )


class StatusLedgerEntryResponse(BaseModel):
    """A single entry from the append-only status ledger."""

    id: UUID = Field(description="Ledger entry UUID.")
    status: str = Field(description="Lifecycle state for this entry.")
    trust_delta: int = Field(description="Incremental trust change for this event.")
    supersede_reason: str | None = Field(
        default=None,
        description="Why this entry was created (null for the initial entry).",
    )
    supersedes: UUID | None = Field(
        default=None,
        description="ID of the ledger entry this row replaces (backward pointer).",
    )
    created_at: AwareDatetime = Field(description="When this entry was appended.")


class ScoringResultResponse(BaseModel):
    """
    Full scoring outcome — returned on POST /submissions and all
    status-transition endpoints.

    Status and trust_delta are read from the latest ``StatusLedger`` row for
    the submission — never from the immutable ``Submission`` row itself.

    ``current_user_vote`` is populated from the requesting user's latest vote
    row for this submission (null if no vote cast yet).  This is provided for
    frontend UX — the canonical state remains the webhook stream.
    """

    submission_id: UUID = Field(description="Echo of the client-supplied submission UUID.")
    project_id: str = Field(min_length=1, description="Project this submission belongs to.")
    entity_type: str = Field(description="Entity type, e.g. 'tree'.")
    entity_id: UUID = Field(description="Domain entity UUID (part of identity triplet).")
    measurement_id: UUID = Field(description="Measurement event UUID (part of identity triplet).")
    ledger_entry_id: UUID = Field(
        description="UUID of the active StatusLedger entry this response reflects.",
    )
    created_at: AwareDatetime = Field(
        description="ISO-8601 timestamp when this submission was first persisted.",
    )
    status: Literal["pending_review", "approved", "rejected", "voided"] = Field(
        description=(
            "Current lifecycle state from the latest StatusLedger row. "
            "'pending_review' = awaiting votes. "
            "'approved'/'rejected' = terminal states. "
            "'voided' = withdrawn, edited, or admin-overridden to void."
        ),
    )
    supersedes: UUID | None = Field(
        default=None,
        description="ID of the ledger entry this row replaces (backward pointer).",
    )
    supersede_reason: str | None = Field(
        default=None,
        description=(
            "Reason the active ledger entry was created. Null for the initial "
            "entry.  Values: edited | deleted | voting_concluded | auto_approve "
            "| auto_reject | admin_overwrite | re-scored."
        ),
    )
    trust_delta: int = Field(
        default=0,
        description="Incremental trust change from the active ledger entry.",
    )
    confidence_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Aggregate Confidence Score (0–100) from the latest scoring snapshot.",
    )
    breakdown: list[RuleBreakdown] = Field(
        description="Per-rule scoring contributions from the latest scoring snapshot.",
    )
    required_validations: list[RequiredValidations] = Field(
        description="All governance tiers applicable to this submission (score >= tier threshold).",
    )
    thresholds: ThresholdConfig = Field(
        description="Project-specific score thresholds for client-side routing.",
    )
    active_votes: list[ActiveVoteItem] = Field(
        default_factory=list,
        serialization_alias="votes",
        description="Latest resolved vote per reviewer (append-only latest-wins).",
    )
