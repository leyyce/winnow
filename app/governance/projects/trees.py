"""
TreeGovernancePolicy — governance rules for the tree-tracking project.

Review tiers and their associated constraints (min_validators, required_min_trust,
required_role) are project-specific and injected at construction time via
GovernanceTier instances. Winnow is the Governance Authority; the client renders
whatever Winnow permits (Rule 5: Domain Ownership).
"""
from __future__ import annotations

from dataclasses import dataclass

from app.governance.base import GovernancePolicy
from app.schemas.envelope import UserContext
from app.schemas.results import RequiredValidations


@dataclass(frozen=True)
class GovernanceTier:
    """
    A single review tier: applies when confidence_score >= score_threshold.

    Tiers should be ordered descending by score_threshold when passed to
    TreeGovernancePolicy — the policy sorts them automatically.
    All values are project-specific and set from the registry.
    """

    score_threshold: float          # minimum CS to fall into this tier
    review_tier: str                # human-readable label, e.g. "peer_review"
    min_validators: int
    required_min_trust: int
    required_role: str | None       # None = any role is eligible


class TreeGovernancePolicy(GovernancePolicy):
    """
    Assigns a review tier by matching the submission's Confidence Score against
    an ordered list of GovernanceTier instances (highest threshold first).

    All threshold values and reviewer constraints are injected from the registry.
    """

    def __init__(self, tiers: list[GovernanceTier]) -> None:
        if not tiers:
            raise ValueError("TreeGovernancePolicy requires at least one GovernanceTier.")
        self._tiers = sorted(tiers, key=lambda t: t.score_threshold, reverse=True)

    def determine_requirements(
        self,
        confidence_score: float,
        user_context: UserContext,
    ) -> RequiredValidations:
        """Match the confidence score to the highest applicable tier."""
        for tier in self._tiers:
            if confidence_score >= tier.score_threshold:
                return RequiredValidations(
                    min_validators=tier.min_validators,
                    required_min_trust=tier.required_min_trust,
                    required_role=tier.required_role,
                    review_tier=tier.review_tier,
                )
        # Fallback: use the lowest tier (last after descending sort)
        fallback = self._tiers[-1]
        return RequiredValidations(
            min_validators=fallback.min_validators,
            required_min_trust=fallback.required_min_trust,
            required_role=fallback.required_role,
            review_tier=fallback.review_tier,
        )

    def is_eligible_reviewer(
        self,
        submission_score: float,
        submission_requirements: RequiredValidations,
        reviewer_trust: int,
        reviewer_role: str,
    ) -> bool:
        """Return True if the reviewer satisfies the submission's review requirements."""
        if reviewer_trust < submission_requirements.required_min_trust:
            return False
        if (
            submission_requirements.required_role is not None
            and reviewer_role != submission_requirements.required_role
        ):
            return False
        return True
