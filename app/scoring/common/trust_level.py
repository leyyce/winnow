"""
TrustLevelRule (Tₙ) — Stage 4 input scoring factor.

Transforms the submitter's current trust level (received on the wire via
UserContext) into a normalised score ∈ [0, 1] using a piecewise-linear
function anchored at a project-configured midpoint and maximum:

    TL ≤ TL_mid  →  Tₙ = 0.5 × TL / TL_mid
    TL > TL_mid  →  Tₙ = 0.5 + 0.5 × (TL − TL_mid) / (TL_max − TL_mid)

Properties:
    TL = 0        → Tₙ = 0.0
    TL = TL_mid   → Tₙ = 0.5
    TL = TL_max   → Tₙ = 1.0  (clamped above TL_max)

TL_mid and TL_max are project-specific and injected at construction time.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.envelope import UserContext
from app.scoring.base import RuleResult, ScoringRule


class TrustLevelRule(ScoringRule):
    """
    Normalises the submitter's trust level into a score using a piecewise-linear
    function. All parameters are injected from project registry configuration.
    """

    def __init__(
        self,
        *,
        weight: float,
        trust_level_mid: int,
        trust_level_max: int,
    ) -> None:
        if trust_level_mid <= 0:
            raise ValueError("trust_level_mid must be > 0")
        if trust_level_max <= trust_level_mid:
            raise ValueError("trust_level_max must be > trust_level_mid")
        self._weight = weight
        self._trust_level_mid = trust_level_mid
        self._trust_level_max = trust_level_max

    @property
    def name(self) -> str:
        return "trust_level"

    @property
    def weight(self) -> float:
        return self._weight

    def evaluate(self, payload: BaseModel, context: UserContext) -> RuleResult:
        tl = context.trust_level

        if tl <= self._trust_level_mid:
            score = 0.5 * tl / self._trust_level_mid
        else:
            over = tl - self._trust_level_mid
            span = self._trust_level_max - self._trust_level_mid
            score = 0.5 + 0.5 * min(over / span, 1.0)

        score = max(0.0, min(1.0, score))

        return RuleResult(
            rule_name=self.name,
            score=score,
            details=(
                f"TL={tl}, TL_mid={self._trust_level_mid}, "
                f"TL_max={self._trust_level_max} → Tₙ={score:.4f}"
            ),
        )
