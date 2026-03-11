"""
Abstract base classes for the Strategy Pattern backbone of Winnow's scoring layer.

Every scoring factor (Hₙ, Aₙ, Pₙ, Kₙ, Tₙ) implements ScoringRule.evaluate()
and is instantiated with project-specific configuration at registry build time.
No numeric values live inside rule implementations — all parameters are injected
from the registry (Rule 3: Configuration is King).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import BaseModel

from app.schemas.envelope import UserContext


@dataclass(frozen=True)
class RuleResult:
    """Normalised output of a single scoring rule evaluation."""

    rule_name: str
    score: float        # normalised ∈ [0.0, 1.0]
    details: str | None = None


class ScoringRule(ABC):
    """
    Abstract scoring strategy.

    Each concrete rule encapsulates one scoring factor. Rules receive a
    *validated* Pydantic payload (Stage 1 has already passed) and the
    UserContext from the wire, and return a normalised RuleResult ∈ [0, 1].

    Weights and all numeric parameters must be injected via __init__ from
    the project's registry configuration — never hardcoded inside the rule.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Machine-readable rule identifier, e.g. 'height_factor'."""

    @property
    @abstractmethod
    def weight(self) -> float:
        """Fractional contribution weight in the pipeline (0–1)."""

    @abstractmethod
    def evaluate(self, payload: BaseModel, context: UserContext) -> RuleResult:
        """Evaluate the payload and return a normalised RuleResult."""
