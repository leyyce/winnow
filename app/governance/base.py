"""
Universal Governance Engine for Winnow.

Sprint 5.1 refactoring
---------------------
``GovernancePolicy`` replaces all project-specific governance
implementations (e.g. ``TreeGovernancePolicy``).  The evaluation math is
identical for every project — only the tier configuration differs, and
that is injected from the project registry (Rule 3: Config is King).

Key behaviours
--------------
* ``determine_requirements`` returns **all** tiers whose
  ``score_threshold <= confidence_score`` (cumulative / multi-tier).
  Every matching tier is an independent pathway to finalization.
* ``get_vote_weight`` evaluates a single ``RequiredValidations`` tier
  snapshot with the three-step blocked → role_configs → default_config
  logic from the Sprint 5 blueprint.
* No project-specific subclasses are needed.  Register a
  ``GovernancePolicy`` instance directly in each project's
  ``ProjectRegistryEntry``.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3.3
* Rule 3:       Configuration is King — no magic numbers in governance logic
* Rule 9:       Services never import fastapi / never raise HTTPException
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.core.exceptions import NotEligibleError
from app.schemas.envelope import UserContext
from app.schemas.results import RequiredValidations, RoleConfig


@dataclass(frozen=True)
class GovernanceTier:
    """
    A single review tier — applies when ``confidence_score >= score_threshold``.

    All values are project-specific and injected from the registry (Rule 3).

    Attributes
    ----------
    confidence_threshold:
        Minimum Confidence Score (0–100) for this tier to apply.
    review_tier:
        Human-readable label, e.g. ``'peer_review'``, ``'expert_review'``.
    vote_threshold:
        Minimum accumulated role-weight needed to finalise a submission.
    role_configs:
        Per-role weight and min_trust map.  Roles absent fall back to
        ``default_config``.
    default_config:
        Fallback config for roles not listed in ``role_configs`` and not
        in ``blocked_roles``.
    blocked_roles:
        Roles that are absolutely ineligible — takes precedence over all.
    """

    confidence_threshold: float
    review_tier: str
    vote_threshold: int
    role_configs: dict[str, RoleConfig]
    default_config: RoleConfig | None = None
    blocked_roles: list[str] = field(default_factory=list)


class GovernancePolicy:
    """
    Project-agnostic governance engine driven entirely by registry config.

    All projects share the same evaluation logic.  Differences in review
    thresholds, role weights, and trust floors are expressed purely through
    the ``GovernanceTier`` list injected at construction time (Rule 3).
    """

    def __init__(self, tiers: list[GovernanceTier]) -> None:
        if not tiers:
            raise ValueError("GovernancePolicy requires at least one GovernanceTier.")
        # Sort descending by score_threshold so iteration is most-restrictive-first
        self._tiers = sorted(tiers, key=lambda t: t.confidence_threshold, reverse=True)

    def determine_requirements(
        self,
        confidence_score: float,
        user_context: UserContext,
    ) -> list[RequiredValidations]:
        """
        Return ALL tiers whose ``score_threshold <= confidence_score``.

        Multiple tiers may match — each represents an independent valid
        pathway to finalization.  The list is ordered most-restrictive first
        (highest score_threshold first).

        If no tier matches (score below all thresholds) the least-restrictive
        tier is returned as a single-element fallback so there is always at
        least one governance pathway.
        """
        matching = [
            RequiredValidations(
                threshold_score=t.vote_threshold,
                role_configs=t.role_configs,
                default_config=t.default_config,
                blocked_roles=t.blocked_roles,
                review_tier=t.review_tier,
            )
            for t in self._tiers
            if confidence_score >= t.confidence_threshold
        ]

        if not matching:
            # Fallback: always return the least-restrictive tier
            fallback = self._tiers[-1]
            matching = [
                RequiredValidations(
                    threshold_score=fallback.vote_threshold,
                    role_configs=fallback.role_configs,
                    default_config=fallback.default_config,
                    blocked_roles=fallback.blocked_roles,
                    review_tier=fallback.review_tier,
                )
            ]

        return matching

    @staticmethod
    def get_vote_weight(
        requirements: RequiredValidations,
        reviewer_role: str,
        reviewer_trust: int,
    ) -> int:
        """
        Return the effective vote weight for an eligible reviewer against
        a specific ``RequiredValidations`` tier snapshot.

        Raises ``NotEligibleError`` (from app.core.exceptions) if the reviewer
        does not meet the eligibility criteria for this tier.

        Evaluation order:
        1. Blocked roles — absolute exclusion, no fallback.
        2. role_configs lookup (or default_config fallback).
        3. Trust floor check.
        4. Return cfg.weight (0 weight → ineligible).
        """
        # Step 1: absolute block — no fallback to default
        if reviewer_role in requirements.blocked_roles:
            raise NotEligibleError(
                f"Role '{reviewer_role}' is permanently blocked from voting."
            )

        # Step 2: role-specific or default config
        cfg = requirements.role_configs.get(reviewer_role, requirements.default_config)

        if cfg is None:
            raise NotEligibleError(
                f"Role '{reviewer_role}' is not configured for voting."
            )

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

    def reaches_threshold(self, threshold: int) -> bool:
        lowest_tier_threshold = min(t.confidence_threshold for t in self._tiers)

        return lowest_tier_threshold <= threshold

__all__ = ["GovernanceTier", "GovernancePolicy"]
