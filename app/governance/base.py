"""
Abstract GovernancePolicy — contract for project-specific governance rules.

Winnow is the Governance Authority: it owns the validation process state and
determines review requirements ("Target State") per submission. The client
(Laravel) acts as a Task Client, rendering whatever Winnow permits.
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
        """Return the review requirements (Target State) for a scored submission."""

    @abstractmethod
    def is_eligible_reviewer(
        self,
        submission_score: float,
        submission_requirements: RequiredValidations,
        reviewer_trust: int,
        reviewer_role: str,
    ) -> bool:
        """Return True if the reviewer may review this submission."""
