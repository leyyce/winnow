"""
Abstract base classes for the Strategy Pattern backbone of Winnow's scoring layer.

Every scoring factor (Hₙ, Aₙ, Pₙ, Kₙ, Tₙ) implements ScoringRule[P] where P is
the project-specific Pydantic payload type.  The concrete ``evaluate()`` method on
the base class performs a centralised runtime type check (replacing per-rule
``assert isinstance`` statements) and then delegates to the abstract ``_evaluate()``
which carries the correct static type signature.

No numeric values live inside rule implementations — all parameters are injected
from the registry (Rule 3: Configuration is King).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.schemas.envelope import UserContext

P = TypeVar("P", bound=BaseModel)


@dataclass(frozen=True)
class RuleResult:
    """Normalised output of a single scoring rule evaluation."""
    rule_name: str
    score: float        # normalised ∈ [0.0, 1.0]
    details: str | None = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(
                f"RuleResult.score must be in [0.0, 1.0], got {self.score!r}"
            )


class ScoringRule(ABC, Generic[P]):
    """
    Abstract scoring strategy, generic over payload type P.

    Each concrete rule declares its expected payload type via the ``payload_type``
    property.  The base ``evaluate()`` method enforces that contract at runtime:
    if the wrong payload type is supplied it raises ``TypeError`` immediately,
    before any rule logic runs.  Concrete rules implement ``_evaluate()`` with a
    fully-typed P signature — no per-rule ``isinstance`` guards are needed.

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

    @property
    @abstractmethod
    def payload_type(self) -> type[P]:
        """The concrete Pydantic model class this rule expects as its payload."""

    def evaluate(self, payload: BaseModel, context: UserContext) -> RuleResult:
        """
        Type-check *payload* against ``payload_type``, then delegate to
        ``_evaluate``.  Raises ``TypeError`` for mismatched payloads so that
        integration errors surface as controlled exceptions rather than silent
        ``AttributeError`` crashes.
        """
        if not isinstance(payload, self.payload_type):
            raise TypeError(
                f"{self.__class__.__name__} expected "
                f"{self.payload_type.__name__}, got {type(payload).__name__}"
            )
        return self._evaluate(payload, context)  # type: ignore[arg-type]

    @abstractmethod
    def _evaluate(self, payload: P, context: UserContext) -> RuleResult:
        """Evaluate the payload and return a normalised RuleResult ∈ [0, 1]."""
