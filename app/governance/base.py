"""
Abstract GovernancePolicy — contract for project-specific governance rules.

Winnow is the Governance Authority: it owns the validation process state and
determines review requirements ("Target State") per submission. The client
(Laravel) acts as a Task Client, rendering whatever Winnow permits.

Sprint 5 upgrade
-----------------
``is_eligible_reviewer`` now implements a three-step evaluation:

1. **Blocked roles** — absolute exclusion, no fallback.
2. **role_configs lookup** — use role-specific weight and min_trust if listed.
3. **default_config fallback** — apply to any role not in role_configs.
4. **Trust floor check** — reject if reviewer_trust < cfg.min_trust.

Eligibility weight is returned as a second value so callers (VotingService)
can accumulate it without a second call.

References
----------
* Architecture: docs/architecture/02_architecture_patterns.md §3.3
* Rule 3:       Configuration is King — no magic numbers in governance logic
* Rule 9:       Services never import fastapi / never raise HTTPException
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.envelope import UserContext
from app.schemas.results import RequiredValidations


class GovernancePolicy(ABC):
    """
    Abstract base for project-specific governance rules.

    Concrete implementations determine who must review a submission (Target
    State) based on the Confidence Score and project-configured review tiers.
    All thresholds and role constraints must be injected via __init__.
    """

    @abstractmethod
    def determine_requirements(
        self,
        confidence_score: float,
        user_context: UserContext,
    ) -> RequiredValidations:
        """
        Return the review requirements (Target State) for a scored submission.

        The returned ``RequiredValidations`` must include the full
        ``role_configs``, ``default_config``, and ``blocked_roles`` snapshot
        so the VotingService can evaluate eligibility from stored data without
        re-calling governance logic.
        """

    @abstractmethod
    def get_vote_weight(
        self,
        requirements: RequiredValidations,
        reviewer_role: str,
        reviewer_trust: int,
    ) -> int:
        """
        Return the effective vote weight for an eligible reviewer.

        Raises ``NotEligibleError`` (from app.core.exceptions) if the reviewer
        does not meet the eligibility criteria for these requirements.

        Evaluation order:
        1. If role is in ``requirements.blocked_roles`` → raise NotEligibleError.
        2. Look up cfg = role_configs.get(role, default_config).
        3. If reviewer_trust < cfg.min_trust → raise NotEligibleError.
        4. Return cfg.weight.
        """
