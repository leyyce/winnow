"""
TreeGovernancePolicy — governance rules for the tree-tracking project.

Review tiers and their associated constraints are project-specific and
injected at construction time via ``GovernanceTier`` instances.  Winnow is
the Governance Authority; the client renders whatever Winnow permits
(Rule 5: Domain Ownership).

Dynamic Governance (role-weights pattern)
-----------------------------------------
Instead of a hard ``required_role`` string and a ``min_validators`` count,
each tier now carries a ``threshold_score`` and a ``role_weights`` dict.
The voting service sums ``role_weights[voter_role]`` for all eligible votes;
when the sum reaches ``threshold_score`` the submission is auto-finalised.

This completely eliminates hardcoded role checks from the service layer —
the service is purely math-agnostic (Rule 3: Configuration is King).

Example:
    threshold_score=2, role_weights={"citizen": 1, "expert": 2}
    → 2 citizen votes (1+1=2) OR 1 expert vote (2) both satisfy the threshold.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.governance.base import GovernancePolicy
from app.schemas.envelope import UserContext
from app.schemas.results import RequiredValidations


@dataclass(frozen=True)
class GovernanceTier:
    """
    A single review tier: applies when ``confidence_score >= score_threshold``.

    Tiers should be ordered descending by ``score_threshold`` when passed to
    ``TreeGovernancePolicy`` — the policy sorts them automatically.

    All values are project-specific and set from the registry (Rule 3).

    Attributes
    ----------
    score_threshold:
        Minimum Confidence Score (0–100) for a submission to fall into this tier.
    review_tier:
        Human-readable label returned to the client, e.g. ``"peer_review"``.
    threshold_score:
        Minimum accumulated role-weight needed to finalise a submission.
    role_weights:
        Maps reviewer role → integer weight contributed per vote. Roles absent
        from this dict (or with weight 0) are ineligible for this tier.
    required_min_trust:
        Minimum trust level a reviewer must hold to cast an eligible vote.
    """

    score_threshold: float
    review_tier: str
    threshold_score: int
    role_weights: dict[str, int]
    required_min_trust: int


class TreeGovernancePolicy(GovernancePolicy):
    """
    Assigns a review tier by matching the submission's Confidence Score against
    an ordered list of ``GovernanceTier`` instances (highest threshold first).

    All threshold values and reviewer constraints are injected from the registry
    — no numeric literals live inside this class (Rule 3: Configuration is King).
    """

    def __init__(self, tiers: list[GovernanceTier]) -> None:
        if not tiers:
            raise ValueError("TreeGovernancePolicy requires at least one GovernanceTier.")
        # Sort descending so the first matching tier is always the most restrictive.
        self._tiers = sorted(tiers, key=lambda t: t.score_threshold, reverse=True)

    def determine_requirements(
        self,
        confidence_score: float,
        user_context: UserContext,
    ) -> RequiredValidations:
        """
        Match the confidence score to the highest applicable tier and return
        its governance requirements as a ``RequiredValidations`` instance.
        """
        for tier in self._tiers:
            if confidence_score >= tier.score_threshold:
                return RequiredValidations(
                    threshold_score=tier.threshold_score,
                    role_weights=tier.role_weights,
                    required_min_trust=tier.required_min_trust,
                    review_tier=tier.review_tier,
                )
        # Fallback: use the lowest tier (last after descending sort).
        fallback = self._tiers[-1]
        return RequiredValidations(
            threshold_score=fallback.threshold_score,
            role_weights=fallback.role_weights,
            required_min_trust=fallback.required_min_trust,
            review_tier=fallback.review_tier,
        )

    def is_eligible_reviewer(
        self,
        submission_score: float,
        submission_requirements: RequiredValidations,
        reviewer_trust: int,
        reviewer_role: str,
    ) -> bool:
        """
        Return ``True`` if the reviewer satisfies the submission's requirements.

        Eligibility requires both:
        1. ``reviewer_trust >= required_min_trust``
        2. ``role_weights.get(reviewer_role, 0) > 0`` — the role must have a
           positive weight contribution in this tier's role_weights dict.
        """
        if reviewer_trust < submission_requirements.required_min_trust:
            return False
        if submission_requirements.role_weights.get(reviewer_role, 0) <= 0:
            return False
        return True
