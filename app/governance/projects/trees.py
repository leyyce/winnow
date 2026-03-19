"""
TreeGovernancePolicy — governance rules for the tree-tracking project.

Sprint 5 upgrade: structured role_configs / default_config / blocked_roles
model replaces the flat role_weights + required_min_trust approach.

This enables:
* Per-role trust minimums (e.g. 'expert' needs 0 trust, 'citizen' needs 50).
* Absolute exclusion of blocked roles ('guest', 'banned') — no fallback.
* A default_config for any role not explicitly listed in role_configs.

All numeric values are injected from the registry (Rule 3: Config is King).
No magic numbers live inside this class.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3.3
* Rule 3:       Configuration is King
* Rule 9:       Services never import fastapi / never raise HTTPException
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.exceptions import NotEligibleError
from app.governance.base import GovernancePolicy
from app.schemas.envelope import UserContext
from app.schemas.results import RequiredValidations, RoleConfig


@dataclass(frozen=True)
class GovernanceTier:
    """
    A single review tier — applies when ``confidence_score >= score_threshold``.

    All values are project-specific and injected from the registry (Rule 3).

    Attributes
    ----------
    score_threshold:
        Minimum Confidence Score (0–100) for a submission to fall into this tier.
    review_tier:
        Human-readable label, e.g. ``'peer_review'``, ``'expert_review'``.
    threshold_score:
        Minimum accumulated role-weight needed to finalise a submission.
    role_configs:
        Per-role weight and min_trust map.  Roles absent fall back to
        ``default_config``.
    default_config:
        Fallback config for roles not listed in ``role_configs`` and not in
        ``blocked_roles``.
    blocked_roles:
        Roles that are absolutely ineligible — takes precedence over everything.
    """

    score_threshold: float
    review_tier: str
    threshold_score: int
    role_configs: dict[str, RoleConfig]
    default_config: RoleConfig
    blocked_roles: list[str] = field(default_factory=list)


class TreeGovernancePolicy(GovernancePolicy):
    """
    Assigns a review tier by matching the submission's Confidence Score against
    an ordered list of ``GovernanceTier`` instances (highest threshold first).

    All threshold values and reviewer constraints are injected from the
    registry — no numeric literals live inside this class (Rule 3).
    """

    def __init__(self, tiers: list[GovernanceTier]) -> None:
        if not tiers:
            raise ValueError("TreeGovernancePolicy requires at least one GovernanceTier.")
        # Sort descending so the first matching tier is always the most restrictive
        self._tiers = sorted(tiers, key=lambda t: t.score_threshold, reverse=True)

    def determine_requirements(
        self,
        confidence_score: float,
        user_context: UserContext,
    ) -> RequiredValidations:
        """
        Match the confidence score to the highest applicable tier and return
        its governance requirements as a ``RequiredValidations`` snapshot.

        The full role_configs / default_config / blocked_roles are embedded
        in the snapshot so the VotingService can evaluate eligibility from
        stored JSONB without re-calling governance logic.
        """
        tier = self._tiers[-1]  # fallback = least restrictive tier
        for t in self._tiers:
            if confidence_score >= t.score_threshold:
                tier = t
                break

        return RequiredValidations(
            threshold_score=tier.threshold_score,
            role_configs=tier.role_configs,
            default_config=tier.default_config,
            blocked_roles=tier.blocked_roles,
            review_tier=tier.review_tier,
        )

    def get_vote_weight(
        self,
        requirements: RequiredValidations,
        reviewer_role: str,
        reviewer_trust: int,
    ) -> int:
        """
        Return the effective vote weight for an eligible reviewer.

        Raises ``NotEligibleError`` if the reviewer is blocked, or does not
        meet the trust floor for their role config.

        Evaluation order (per blueprint §3.3):
        1. Blocked roles — absolute exclusion, no fallback.
        2. role_configs lookup (or default_config fallback).
        3. Trust floor check.
        4. Return cfg.weight.
        """
        # Step 1: absolute block — no fallback to default
        if reviewer_role in requirements.blocked_roles:
            raise NotEligibleError(
                f"Role '{reviewer_role}' is permanently blocked from voting."
            )

        # Step 2: role-specific or default config
        cfg = requirements.role_configs.get(reviewer_role, requirements.default_config)

        # Step 3: trust floor
        if reviewer_trust < cfg.min_trust:
            raise NotEligibleError(
                f"Trust level {reviewer_trust} is below the required "
                f"{cfg.min_trust} for role '{reviewer_role}'."
            )

        # Step 4: weight=0 means this role is effectively ineligible
        if cfg.weight == 0:
            raise NotEligibleError(
                f"Role '{reviewer_role}' has weight 0 and cannot contribute to voting."
            )

        return cfg.weight
